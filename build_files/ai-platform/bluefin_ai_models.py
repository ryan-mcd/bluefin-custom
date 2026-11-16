"""Rootless management of the approved platform model catalog and cached data."""
import hashlib
import http.client
import json
import re
import subprocess
from pathlib import Path
from bluefin_ai_huggingface import model_path, registry_profiles, release_cache_revisions


def same_model(left, right):
    def identity(profile):
        if profile.get('source'):
            source = profile['source']
            return source['repo'], source['revision'], source.get('file'), source['format']
        return profile['engine'], profile['backend_model'], sorted((a['name'], a['sha256'], a['bytes']) for a in profile['artifacts'])
    return identity(left) == identity(right)


def artifact_ready(models, artifact):
    path = model_path(models, artifact['name'])
    try:
        if path.is_symlink() or not path.is_file():
            return False
        stat = path.stat()
        expected = dict(name=artifact['name'], sha256=artifact['sha256'], bytes=artifact['bytes'],
                        stat=[getattr(stat, field) for field in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')])
        receipt_id = hashlib.sha256((artifact['name'] + ':' + artifact['sha256']).encode()).hexdigest()
        return stat.st_size == artifact['bytes'] and json.loads((models / '.verified' / (receipt_id + '.json')).read_text()) == expected
    except (OSError, ValueError, TypeError):
        return False


def selected_profile(config, base, *, saved_only=False):
    fallback = None if saved_only else config['aliases']['general']
    try:
        profile = json.loads((base / 'selection.json').read_text())['profile']
        return profile if isinstance(profile, str) and profile in config['profiles'] else fallback
    except (OSError, ValueError, KeyError, TypeError):
        return fallback


class ModelManager:
    def __init__(self, config, base, models, run, write, port=18100):
        self.config, self.base, self.models = config, base, models
        self.run, self.write, self.port = run, write, port

    def installed(self):
        path = self.base / 'installed-models.json'
        if not path.exists():
            return set()
        data = json.loads(path.read_text())
        value = data.get('profiles') if isinstance(data, dict) else None
        if not isinstance(value, list) or any(not isinstance(p, str) for p in value):
            raise ValueError('Invalid installed-models.json; expected a profiles array')
        return set(value).intersection(self.config['profiles'])

    def remember(self, profiles):
        self.write(self.base / 'installed-models.json', json.dumps({'profiles': sorted(profiles)}) + '\n')

    def boot_profiles(self):
        bootstrap = self.config.get('prepare_on_boot', list(self.config['profiles']))
        selected = selected_profile(self.config, self.base, saved_only=True)
        wanted = set(bootstrap) | self.installed() | ({selected} if selected else set())
        return [name for name in self.config['profiles'] if name in wanted]

    def resolve(self, name, context=None):
        name = name.removeprefix('local/')
        profile = selected_profile(self.config, self.base) if name == 'general' else self.config['aliases'].get(name, name)
        if profile not in self.config['profiles']:
            matches = [key for key, value in self.config['profiles'].items()
                       if value.get('source', {}).get('repo') == name]
            if len(matches) != 1:
                raise ValueError('Choose an installed model ID from models list; repository selection is missing or ambiguous')
            profile = matches[0]
        if context is None:
            return profile
        base = self.config['profiles'][profile]
        matches = [key for key, value in self.config['profiles'].items()
                   if same_model(base, value) and value.get('context_tokens') == context]
        if len(matches) != 1:
            raise ValueError('That context is not configured for this model; run models list')
        return matches[0]

    def control(self, action=None, profile=None, purge_cache=False):
        credential = (self.base / 'admin-key').read_text().strip()
        if not re.fullmatch(r'[0-9a-f]{64}', credential):
            raise ValueError('Invalid controller credential file')
        connection = http.client.HTTPConnection('127.0.0.1', self.port,
                                                timeout=self.config['startup_seconds'] + self.config['drain_seconds'] + 60)
        try:
            headers = {'Authorization': 'Bearer ' + credential, 'Content-Type': 'application/json'}
            payload = json.dumps(dict(action=action, profile=profile, purge_cache=purge_cache)) if action else None
            connection.request('POST' if action else 'GET', '/control/mode' if action else '/control/status', payload, headers)
            response = connection.getresponse()
            body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise RuntimeError('Oversized controller response')
            data = json.loads(body)
            if response.status != 200:
                raise RuntimeError(data.get('error', {}).get('message', 'Controller request failed'))
            return data
        except (OSError, http.client.HTTPException) as exc:
            raise RuntimeError('Cannot reach the runtime controller; check bluefin-ai-platform status') from exc
        finally:
            connection.close()

    def image_ready(self, profile):
        result = subprocess.run(['podman', 'image', 'exists', profile['image']], capture_output=True, text=True, timeout=30)
        if result.returncode not in (0, 1):
            raise RuntimeError('Cannot inspect the runtime image cache: ' + result.stderr.strip())
        return result.returncode == 0

    def list_models(self):
        installed = self.installed()
        selected = selected_profile(self.config, self.base)
        print('PROFILE                         CONTEXT  RUNTIME    CACHE        POLICY')
        images = {}
        for name, profile in self.config['profiles'].items():
            image = profile['image']
            if image not in images:
                images[image] = self.image_ready(profile)
            weights = all(artifact_ready(self.models, a) for a in profile['artifacts'])
            state = 'ready' if weights and images[image] else 'image needed' if weights else 'fetch needed'
            policy = 'boot' if name in self.config.get('prepare_on_boot', list(self.config['profiles'])) else 'installed' if name in installed else 'on demand'
            if name == selected:
                policy += ', selected'
            if profile.get('source'):
                source = profile['source']
                print('  ' + source['repo'] + '@' + source['revision'][:12] + ' / ' + str(source.get('file') or source['format']))
            print(f'{name:31} {profile.get("context_tokens", "?")!s:8} {profile["engine"]:10} {state:12} {policy}')

    def plan(self, profiles):
        unique = {a['name']: a for name in profiles for a in self.config['profiles'][name]['artifacts']}
        needed = sum(a['bytes'] for a in unique.values() if not artifact_ready(self.models, a))
        for name in profiles:
            profile = self.config['profiles'][name]
            print(f'{name}: {profile["engine"]}, {profile.get("context_tokens", "?")} tokens, {profile["resident_gib"]} GiB admission budget')
        for artifact in unique.values():
            print(f'  {artifact["name"]}: {artifact["bytes"] / 10**9:.2f} GB')
        print(f'Unverified/missing weights: {needed / 10**9:.2f} GB. Runtime images and prepared caches need additional space.')

    def remove_files(self, profile_name, purge_cache=False):
        profile = self.config['profiles'][profile_name]
        group = {name for name, value in self.config['profiles'].items() if same_model(profile, value)}
        bootstrap = set(self.config.get('prepare_on_boot', list(self.config['profiles'])))
        if group & bootstrap:
            raise ValueError('This model is prepared by the image at boot; change prepare_on_boot before removing it')
        selected = selected_profile(self.config, self.base)
        if selected in group:
            raise ValueError('Select another default model before removing this one')
        remaining = self.installed() - group
        references = (set(self.config['profiles']) - group) | bootstrap | ({selected} if selected else set())
        kept_files = {a['name'] for name in references for a in self.config['profiles'][name]['artifacts']}
        candidates = [a for a in profile['artifacts'] if a['name'] not in kept_files]
        for artifact in candidates:
            model_path(self.models, artifact['name'])
        self.remember(remaining)
        if purge_cache:
            def volume(name):
                return self.config['profiles'][name].get('cache_volume', 'bluefin-runtime-' + name + '-cache')
            kept_volumes = {volume(name) for name in references}
            for cache in sorted({volume(name) for name in group} - kept_volumes):
                result = subprocess.run(['podman', 'volume', 'exists', cache], capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    self.run('podman', 'volume', 'rm', cache, timeout=60)
                elif result.returncode != 1:
                    raise RuntimeError('Cannot inspect prepared-data volume: ' + result.stderr.strip())
        for artifact in candidates:
            path = model_path(self.models, artifact['name'])
            path.unlink(missing_ok=True)
            for parent in path.parents:
                if parent == self.models:
                    break
                try:
                    parent.rmdir()
                except OSError:
                    break
            receipt_id = hashlib.sha256((artifact['name'] + ':' + artifact['sha256']).encode()).hexdigest()
            (self.models / '.verified' / (receipt_id + '.json')).unlink(missing_ok=True)
        release_cache_revisions([self.config['profiles'][name] for name in group],
                                [self.config['profiles'][name] for name in references])
        registry = registry_profiles(self.base)
        for name in group:
            registry.pop(name, None)
            (Path.home() / '.config/containers/systemd' / ('bluefin-runtime-' + name + '.container')).unlink(missing_ok=True)
        self.write(self.base / 'model-registry.json', json.dumps(dict(version=1, profiles=registry)) + '\n')
        self.run('systemctl', '--user', 'daemon-reload', timeout=60)
        print('Removed unshared weights and installation intent. Prepared data ' + ('purged where unshared.' if purge_cache else 'retained; --purge-cache also removes it.'))
