const $ = (selector) => document.querySelector(selector);
const fragment = location.hash.slice(1);
if (fragment) { sessionStorage.setItem('bluefin-dashboard-capability', fragment); history.replaceState(null, '', location.pathname); }
const capability = sessionStorage.getItem('bluefin-dashboard-capability') || '';
const authorization = {'Authorization': `Bearer ${capability}`};
const descriptions = {
  'disabled': 'Stops managed AI services and removes the model Quadlet. Apply the firewall step afterward to remove managed remote rules.',
  'model-only': 'Runs RamaLama on loopback only. No Hermes or remote model gateway services are started.',
  'central-agent': 'Runs the local model and Hermes services. Hermes must already be installed. Remote Hermes access requires the separate firewall step.',
  'remote-agents': 'Runs the local model and authenticated model gateway for remote agents. The separate firewall step is required.',
  'hybrid': 'Runs the local model, Hermes services, and authenticated model gateway. Hermes must already be installed; apply the firewall separately.'
};
let busy = false;
let lastState = '';
let loaded = false;
function showNotice(message) { const el = $('#notice'); el.textContent = message; el.classList.toggle('hidden', !message); }
function description() { $('#profile-description').textContent = descriptions[$('#profile').value]; }
function setBusy(value) { busy = value; document.querySelectorAll('button').forEach(button => { if (button.id !== 'refresh') button.disabled = value; }); }
async function refresh() {
  try {
    const response = await fetch('/api/status', {cache: 'no-store', headers: authorization});
    if (response.status === 401) throw new Error('Open the dashboard using the private address printed in the terminal.');
    if (!response.ok) throw new Error('Status request failed');
    const data = await response.json();
    const c = data.config;
    $('#hero-model').textContent = c.MODEL || 'Model unavailable';
    $('#hero-description').textContent = c.PROFILE === 'disabled' ? 'The workspace is disabled. Choose a model or profile to get started.' : 'Your selected model is managed by RamaLama on this machine.';
    $('#hero-profile').textContent = `PROFILE / ${c.PROFILE || 'UNKNOWN'}`.toUpperCase();
    $('#hero-context').textContent = `CONTEXT / ${c.MODEL_CONTEXT_LENGTH ? Number(c.MODEL_CONTEXT_LENGTH).toLocaleString() : '—'}`;
    $('#endpoint').textContent = `127.0.0.1 / ${c.MODEL_PORT || '—'}`;
    $('#readiness').textContent = data.ready ? 'MODEL ENDPOINT READY' : 'MODEL ENDPOINT NOT READY';
    $('#ready-dot').classList.toggle('ok', data.ready);
    $('#models-list').textContent = data.models || (data.models_error ? 'Unable to list models.' : 'No models returned.');
    if (!loaded) {
      $('#model').value = c.MODEL || '';
      $('#context').value = c.MODEL_CONTEXT_LENGTH || 64000;
      if (descriptions[c.PROFILE]) $('#profile').value = c.PROFILE;
      description(); loaded = true;
    }
    const grid = $('#services'); grid.replaceChildren();
    Object.entries(data.services).forEach(([name, state]) => {
      const card = document.createElement('div'); card.className = 'health';
      const top = document.createElement('div'); top.className = 'health-top'; top.textContent = name;
      const icon = document.createElement('span'); icon.textContent = '↗'; top.append(icon);
      const value = document.createElement('strong'); const dot = document.createElement('i'); dot.className = state; value.append(dot, document.createTextNode(state.charAt(0).toUpperCase() + state.slice(1)));
      card.append(top, value); grid.append(card);
    });
    const job = data.job;
    setBusy(job.state === 'running');
    $('#job-state').textContent = job.state === 'idle' ? 'READY' : job.state.toUpperCase();
    $('#activity-message').textContent = job.state === 'idle' ? 'Actions you run here will appear below.' : job.state === 'running' ? 'Operation in progress. Model downloads and startup can take several minutes.' : `${job.action === 'model' ? 'Model selection' : job.action === 'profile' ? 'Profile change' : 'Hermes connection'} ${job.state}.`;
    $('#job-output').textContent = job.output; $('#job-output').classList.toggle('hidden', !job.output);
    if (lastState === 'running' && job.state !== 'running') {
      if (c.MODEL) $('#model').value = c.MODEL;
      if (c.MODEL_CONTEXT_LENGTH) $('#context').value = c.MODEL_CONTEXT_LENGTH;
      if (descriptions[c.PROFILE]) $('#profile').value = c.PROFILE;
      description();
    }
    lastState = job.state;
    showNotice(data.errors.length ? `Configuration unavailable: ${data.errors.join(' · ')}` : '');
  } catch (error) { showNotice(error.message.includes('private address') ? error.message : 'Cannot reach the dashboard service. Check that it is still running, then refresh.'); }
}
async function action(payload) {
  if (busy) return;
  setBusy(true); showNotice('');
  try {
    const response = await fetch('/api/action', {method: 'POST', headers: {...authorization, 'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Unable to start operation.');
    lastState = 'running';
    await refresh();
    $('#activity').scrollIntoView({behavior: 'smooth', block: 'nearest'});
  } catch (error) { setBusy(false); showNotice(error.message); }
}
$('#model-form').addEventListener('submit', event => { event.preventDefault(); action({action: 'model', model: $('#model').value.trim(), context: $('#context').value}); });
$('#profile-form').addEventListener('submit', event => { event.preventDefault(); const profile = $('#profile').value; if (confirm(`Apply the ${profile} profile? This restarts managed services. Remote profiles require a separate administrator firewall step.`)) action({action: 'profile', profile}); });
$('#hermes').addEventListener('click', () => { if (confirm('Configure the existing Hermes installation to use the selected model?')) action({action: 'hermes'}); });
$('#profile').addEventListener('change', description);
$('#refresh').addEventListener('click', refresh);
description(); refresh(); setInterval(refresh, 8000);
