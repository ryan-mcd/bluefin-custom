"""Hugging Face source resolution; model choices are host data, not OS policy."""
import hashlib
import io
import json
import math
import os
import shutil
import re
import struct
import urllib.parse
from pathlib import Path, PurePosixPath

GIB = 1024 ** 3


def relative_name(value):
    if (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_./-]*', value)
            or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError('Unsupported or unsafe repository file path: ' + str(value))
    return value


def model_path(root, name):
    relative_name(name)
    if root.is_symlink():
        raise ValueError('Refusing symlink in model storage')
    path = root
    for part in PurePosixPath(name).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('Refusing symlink in model storage')
    return path


def hub_client():
    # Keep static manifest validation independent of optional source operations.
    import huggingface_hub
    return huggingface_hub


def hub_failure(exc):
    # Upstream exceptions can contain signed URLs; expose only a safe summary.
    status = getattr(getattr(exc, 'response', None), 'status_code', None)
    detail = (' HTTP ' + str(status)) if isinstance(status, int) else ''
    raise ValueError('Hugging Face request failed' + detail + ' (' + type(exc).__name__ +
                     '); check hf auth whoami, source access and network connectivity') from None


class Hub:
    def __init__(self):
        client = hub_client()
        self.api, self.fs = client.HfApi(), client.HfFileSystem()

    def info(self, repo, revision):
        try:
            info = self.api.model_info(repo, revision=revision, files_metadata=True)
            siblings = []
            for item in info.siblings or []:
                lfs = item.lfs
                siblings.append(dict(rfilename=item.rfilename, size=item.size, blobId=item.blob_id,
                                     lfs=(lfs if isinstance(lfs, dict) else dict(sha256=lfs.sha256, size=lfs.size)) if lfs else None))
            return dict(sha=info.sha, siblings=siblings)
        except Exception as exc:
            hub_failure(exc)

    def file(self, repo, revision, name, prefix=False):
        limit = 16 * 1024 * 1024
        try:
            # Range reads and auth/retries belong to the official filesystem API.
            with self.fs.open(repo + '/' + name, revision=revision, mode='rb',
                              block_size=1024 * 1024, cache_type='none') as stream:
                data = stream.read(limit if prefix else limit + 1)
        except Exception as exc:
            hub_failure(exc)
        if len(data) > limit:
            raise ValueError('Repository metadata exceeds size limit')
        return data


def cache_directory(profile=None):
    value = (profile or {}).get('hub_cache')
    if value is None:
        from huggingface_hub.constants import HF_HUB_CACHE
        value = HF_HUB_CACHE
    return Path(value).expanduser().resolve()


def artifact_source(artifact):
    url = artifact.get('url', '')
    if not url:
        raise ValueError('Model is missing; preseed it or fetch a Hugging Face source')
    repo, revision, filename = parse_source(url)
    if not filename or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Legacy downloads require an exact Hugging Face file/commit; re-fetch the source')
    return repo, revision, filename


def download_artifact(artifact, cache_dir=None):
    repo, revision, filename = artifact_source(artifact)
    client = hub_client()
    cache = Path(cache_dir) if cache_dir is not None else cache_directory()
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        plan = client.hf_hub_download(repo_id=repo, filename=filename, revision=revision,
                                      cache_dir=cache, dry_run=True)
    except Exception as exc:
        hub_failure(exc)
    if plan.will_download and shutil.disk_usage(cache).free < artifact['bytes'] + 8 * GIB:
        raise ValueError('Insufficient disk space in Hugging Face cache plus 8 GiB reserve')
    try:
        return Path(client.hf_hub_download(repo_id=repo, filename=filename, revision=revision,
                                           cache_dir=cache))
    except Exception as exc:
        hub_failure(exc)


def cache_references(profile):
    result = set()
    for artifact in profile['artifacts']:
        try:
            repo, revision, _ = artifact_source(artifact)
        except ValueError:
            continue  # Preseeded legacy artifacts may have no HF provenance.
        result.add((str(cache_directory(profile)), repo, revision))
    return result


def release_cache_revisions(removed, remaining):
    """Delete only unreferenced model revisions via the official cache API."""
    candidates = set().union(*(cache_references(p) for p in removed))
    protected = set().union(*(cache_references(p) for p in remaining))
    for root, repo, revision in sorted(candidates - protected):
        cache = Path(root)
        if not cache.is_dir():
            continue
        try:
            info = hub_client().scan_cache_dir(cache)
            owners = [item for item in info.repos if any(r.commit_hash == revision for r in item.revisions)]
            # delete_revisions identifies commits globally. A fork can share a
            # commit hash; never evict an unrelated repository in that case.
            if len(owners) != 1 or owners[0].repo_id != repo or owners[0].repo_type != 'model':
                continue
            info.delete_revisions(revision).execute()
        except Exception as exc:
            hub_failure(exc)


def file_url(repo, revision, name):
    return 'https://huggingface.co/' + repo + '/resolve/' + revision + '/' + urllib.parse.quote(name, safe='/')


def parse_source(source, revision=None, filename=None):
    if source.startswith('https://'):
        url = urllib.parse.urlsplit(source)
        if url.hostname != 'huggingface.co' or url.username or url.password or url.query or url.fragment:
            raise ValueError('Supply a Hugging Face model repository or resolve/blob URL')
        parts = url.path.strip('/').split('/')
        repo = '/'.join(parts[:2])
        if len(parts) > 2:
            if len(parts) < 4 or parts[2] not in ('resolve', 'blob', 'tree'):
                raise ValueError('Unsupported Hugging Face URL')
            embedded_revision = urllib.parse.unquote(parts[3])
            embedded_file = urllib.parse.unquote('/'.join(parts[4:])) or None
            if revision is not None and revision != embedded_revision:
                raise ValueError('Conflicting URL and --revision')
            revision = embedded_revision
            if embedded_file:
                if filename is not None and filename != embedded_file:
                    raise ValueError('Conflicting URL and --file')
                filename = embedded_file
    else:
        source = source.removeprefix('hf://').removeprefix('huggingface://')
        parts = source.split('/')
        repo = '/'.join(parts[:2])
        if len(parts) > 2:
            if filename is not None:
                raise ValueError('Supply the file once')
            filename = '/'.join(parts[2:])
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*', repo):
        raise ValueError('Use owner/repository, hf://owner/repository, or a Hugging Face URL')
    if revision is not None and (not revision or len(revision) > 200 or any(ord(c) < 32 for c in revision)):
        raise ValueError('Invalid revision')
    if filename:
        relative_name(filename)
    return repo, revision or 'main', filename


def gguf_metadata(data):
    stream = io.BytesIO(data)
    def unpack(fmt):
        size = struct.calcsize('<' + fmt)
        raw = stream.read(size)
        if len(raw) != size:
            raise ValueError('GGUF metadata exceeds the inspected header or is truncated')
        return struct.unpack('<' + fmt, raw)[0]
    def string():
        length = unpack('Q')
        if length > len(data) - stream.tell():
            raise ValueError('GGUF string exceeds inspected header')
        return stream.read(length).decode('utf-8')
    def value(kind, keep=True):
        formats = {0:'B', 1:'b', 2:'H', 3:'h', 4:'I', 5:'i', 6:'f', 7:'?', 10:'Q', 11:'q', 12:'d'}
        if kind in formats:
            return unpack(formats[kind])
        if kind == 8:
            return string()
        if kind == 9:
            item, count = unpack('I'), unpack('Q')
            if item == 9 or count > 2_000_000:
                raise ValueError('Invalid GGUF metadata array')
            for _ in range(count):
                value(item, False)
            return None
        raise ValueError('Unknown GGUF metadata type')
    if stream.read(4) != b'GGUF' or unpack('I') not in (2, 3):
        raise ValueError('Source is not a supported GGUF file')
    unpack('Q')  # tensor count
    count = unpack('Q')
    if count > 100000:
        raise ValueError('Excessive GGUF metadata')
    result = {}
    for _ in range(count):
        key = string()
        kind = unpack('I')
        result[key] = value(kind)
    return {key: val for key, val in result.items() if val is not None and not key.startswith('tokenizer.')}


def shard_files(filename, files):
    match = re.fullmatch(r'(.*)-(\d{5})-of-(\d{5})\.gguf', filename)
    if not match:
        return [filename]
    stem, _, total = match.groups()
    count = int(total)
    if not 1 <= count <= 1000:
        raise ValueError('Invalid GGUF shard count')
    names = [f'{stem}-{i:05d}-of-{count:05d}.gguf' for i in range(1, count + 1)]
    if not set(names) <= set(files):
        raise ValueError('The repository is missing GGUF shards')
    return names


def resolve_source(source, revision=None, filename=None, hub=None, format="auto"):
    hub = hub or Hub()
    repo, revision, filename = parse_source(source, revision, filename)
    info = hub.info(repo, revision)
    commit = info.get('sha', '')
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Hub did not return an immutable commit')
    files = {item['rfilename']: item for item in info.get('siblings', [])}
    if len(files) > 100000:
        raise ValueError('Excessive repository file count')
    ggufs = [name for name in files if name.lower().endswith('.gguf') and not PurePosixPath(name).name.lower().startswith(('mmproj', 'mtp-'))]
    if format not in ('auto', 'gguf', 'safetensors'):
        raise ValueError('Unsupported model format')
    if format == 'safetensors' and filename:
        raise ValueError('Do not combine a GGUF --file with --format safetensors')
    if format == 'safetensors':
        ggufs = []
    if format == 'auto' and not filename and ggufs and 'config.json' in files and any(name.endswith('.safetensors') for name in files):
        raise ValueError('Source contains GGUF and native weights; choose --file or --format safetensors')
    choices = sorted({re.sub(r'-\d{5}-of-(\d{5})\.gguf$', r'-00001-of-\1.gguf', name) for name in ggufs})
    if filename is None and choices:
        if len(choices) != 1:
            raise ValueError('Choose an exact --file; this repository has multiple GGUF variants:\n' + '\n'.join(choices))
        filename = choices[0]
    if filename:
        if filename not in files or not filename.lower().endswith('.gguf'):
            raise ValueError('--file must name a GGUF weight file in the selected repository')
        names = shard_files(filename, files)
        filename = names[0]
        metadata = gguf_metadata(hub.file(repo, commit, filename, prefix=True))
        architecture = metadata.get('general.architecture')
        maximum = metadata.get(str(architecture) + '.context_length')
        fmt = 'gguf'
    else:
        if format == 'gguf':
            raise ValueError('No GGUF weights found in the repository')
        if 'config.json' not in files:
            raise ValueError('No GGUF or native checkpoint with config.json found')
        metadata = json.loads(hub.file(repo, commit, 'config.json'))
        if not isinstance(metadata, dict):
            raise ValueError('config.json must be an object')
        text = metadata.get('text_config', metadata)
        if not isinstance(text, dict):
            raise ValueError('text_config must be an object')
        architecture = text.get('model_type', metadata.get('model_type'))
        maximum = text.get('max_position_embeddings', text.get('n_positions'))
        if 'model.safetensors.index.json' in files:
            index = json.loads(hub.file(repo, commit, 'model.safetensors.index.json'))
            if not isinstance(index, dict) or not isinstance(index.get('weight_map'), dict):
                raise ValueError('Invalid safetensors index')
            weights = list(set(index['weight_map'].values()))
            if any(not isinstance(name, str) for name in weights):
                raise ValueError('Invalid safetensors index filenames')
            weights.sort()
        elif 'model.safetensors' in files:
            weights = ['model.safetensors']
        else:
            raise ValueError('No standard safetensors checkpoint found; select a GGUF file or supported native snapshot')
        if not weights or any(not name.endswith('.safetensors') or name not in files for name in weights):
            raise ValueError('Invalid or incomplete safetensors index')
        # Preserve native layout, tokenizer and config; never fetch Python/pickle code.
        assets = [name for name in files if '/' not in name and (name.endswith(('.json', '.model', '.txt', '.tiktoken', '.jinja')))]
        names = sorted(set(weights + assets))
        fmt = 'safetensors'
    namespace = 'hf-' + hashlib.sha256((repo + '@' + commit).encode()).hexdigest()[:20]
    artifacts = []
    for name in names:
        relative_name(name)
        item = files[name]
        lfs = item.get('lfs') or {}
        size = item.get('size', lfs.get('size'))
        checksum = lfs.get('sha256')
        if not checksum:
            if not isinstance(size, int) or size > 16 * 1024 * 1024:
                raise ValueError('Missing content hash for large source file: ' + name)
            raw = hub.file(repo, commit, name)
            blob = item.get('blobId', item.get('blob_id', ''))
            if blob and hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw, usedforsecurity=False).hexdigest() != blob:
                raise ValueError('Git blob checksum mismatch: ' + name)
            checksum = hashlib.sha256(raw).hexdigest()
            if len(raw) != size:
                raise ValueError('Source file size mismatch: ' + name)
        if not isinstance(size, int) or size < 1 or not re.fullmatch(r'[0-9a-f]{64}', checksum):
            raise ValueError('Missing immutable file metadata: ' + name)
        artifacts.append(dict(name=namespace + '/' + name, sha256=checksum, bytes=size,
                              url=file_url(repo, commit, name)))
    return dict(repo=repo, revision=commit, requested_revision=revision, file=filename, format=fmt,
                architecture=architecture, max_context=maximum, metadata=metadata,
                directory=namespace, artifacts=artifacts)


def make_profile(source, images, runtime='auto', context=None, memory_gib=None):
    engine = ('ramalama' if source['format'] == 'gguf' else 'vllm') if runtime == 'auto' else runtime
    if engine not in ('ramalama', 'llamacpp', 'strata', 'vllm'):
        raise ValueError('Unsupported runtime')
    if source['format'] != 'gguf' and engine != 'vllm':
        raise ValueError('Native checkpoints require the vLLM adapter; select a GGUF source for other runtimes')
    if source['format'] == 'gguf' and engine == 'vllm':
        raise ValueError('Select a native safetensors checkpoint for the vLLM adapter')
    maximum = source.get('max_context')
    if type(maximum) is not int or maximum < 1:
        if context is None:
            raise ValueError('Source does not declare context length; supply --context explicitly')
    elif context is not None and context > maximum:
        raise ValueError(f'Requested context exceeds source metadata ({maximum} tokens)')
    tokens = context if context is not None else min(65536, maximum)
    if not 1 <= tokens <= 262144:
        raise ValueError('Context must be between 1 and 262144 tokens')
    meta = source['metadata']
    arch = str(source.get('architecture'))
    if source['format'] == 'gguf':
        layers = meta.get(arch + '.block_count')
        heads = meta.get(arch + '.attention.head_count')
        kv_heads = meta.get(arch + '.attention.head_count_kv', heads)
        embedding = meta.get(arch + '.embedding_length')
        key_dim = meta.get(arch + '.attention.key_length') or (embedding // heads if type(embedding) is int and type(heads) is int and heads > 0 else None)
        value_dim = meta.get(arch + '.attention.value_length', key_dim)
    else:
        text = meta.get('text_config', meta)
        layers, heads = text.get('num_hidden_layers'), text.get('num_attention_heads')
        kv_heads = text.get('num_key_value_heads', heads)
        key_dim = text.get('head_dim') or (text['hidden_size'] // heads if type(text.get('hidden_size')) is int and type(heads) is int and heads > 0 else None)
        value_dim = key_dim
    geometry = (layers, kv_heads, key_dim, value_dim)
    weights = sum(a['bytes'] for a in source['artifacts'])
    estimate = None
    if all(type(value) is int and value > 0 for value in geometry):
        # Conservative FP16 K/V; Strata's int8 is an implementation optimization.
        kv = tokens * layers * kv_heads * (key_dim + value_dim) * 2
        estimate = math.ceil((weights + kv) / GIB + max(4, weights / GIB * .15))
    budget = memory_gib if memory_gib is not None else estimate
    if budget is None:
        raise ValueError('Cannot estimate KV memory from source metadata; supply --memory-gib explicitly')
    if type(budget) is not int or not 1 <= budget <= 110:
        raise ValueError('Estimated memory exceeds the 110 GiB runtime limit; reduce --context or choose smaller weights')
    if budget * GIB < weights + 2 * GIB:
        raise ValueError('Memory budget cannot hold the selected weights and runtime overhead')
    identity = dict(repo=source['repo'], revision=source['revision'], file=source['file'], runtime=engine, context=tokens, memory_gib=budget)
    name = 'hf-' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    cache = 'bluefin-model-' + hashlib.sha256(json.dumps({k:v for k,v in identity.items() if k not in ('context', 'memory_gib')}, sort_keys=True).encode()).hexdigest()[:24]
    image = images.get(engine)
    if not image:
        raise ValueError('Runtime image is not configured: ' + engine)
    profile = dict(engine=engine, image=image, accelerator='vulkan' if engine in ('ramalama','llamacpp') else 'rocm',
                   requires_key=True, backend_model=name, resident_gib=budget, context_tokens=tokens,
                   port=18110, cache_volume=cache, artifacts=source['artifacts'], source=source,
                   model_directory=source['directory'], command=[])
    if engine in ('ramalama', 'llamacpp'):
        profile['command'] = (['llama-server'] if engine == 'ramalama' else []) + [
            '--host','0.0.0.0','--port','8080','--model','/models/' + source['directory'] + '/' + source['file'],  # nosec B104: container listener, loopback-only host publication
            '--alias',name,'--ctx-size',str(tokens),'--parallel','1','--n-gpu-layers','99','--jinja',
            '--api-key-file','/run/bluefin-key']
    elif engine == 'strata':
        profile['discover_backend_model'] = True
        # No FAMILY/MODEL defaults: the exact GGUF source drives Strata discovery.
        profile['environment'] = dict(MODEL_PATH='/models/' + source['directory'] + '/' + source['file'],
                                      CONTEXT=str(tokens), PARALLEL='1', KV='int8', VISION='off', TUNE='skip',
                                      API_KEY_FILE='/run/bluefin-key', PRESET=name, STRATA_UMA_RESERVE_GIB='16')
    else:
        profile['command'] = ['--host','0.0.0.0','--port','8080','--model','/models/' + source['directory'],  # nosec B104: container listener, loopback-only host publication
                              '--served-model-name',name,'--max-model-len',str(tokens),'--max-num-seqs','1',
                              '--kv-cache-memory-bytes',str(max(GIB, (budget * GIB - weights - 4 * GIB)))]
    return name, profile


def registry_profiles(base):
    path = base / 'model-registry.json'
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('profiles'), dict):
        raise ValueError('Invalid model-registry.json')
    return data['profiles']


def merge_registry(config, base):
    profiles = registry_profiles(base)
    overlap = set(config['profiles']) & set(profiles)
    if overlap:
        raise ValueError('Installed model IDs collide with image profiles')
    rebound = {name: dict(profile, image=config.get('runtime_images', {}).get(profile['engine'], profile['image']))
               for name, profile in profiles.items()}
    return dict(config, profiles={**config['profiles'], **rebound})
