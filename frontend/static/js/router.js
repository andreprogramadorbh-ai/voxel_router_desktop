(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const state = { system: null, user: null };
  const labels = { 1: 'CRITICAL', 2: 'HIGH', 3: 'NORMAL', 4: 'LOW' };

  // A interface é uma SPA local. A posição anterior nunca deve reaparecer após autenticação ou troca de seção.
  if ('scrollRestoration' in history) history.scrollRestoration = 'manual';

  function scrollToTop() {
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options });
    if (response.status === 204) return null;
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || 'Não foi possível concluir a operação.');
    return payload;
  }

  function showMessage(text, isError = true) {
    const message = $('#login-message');
    message.textContent = text;
    message.style.color = isError ? 'var(--red)' : 'var(--green)';
  }

  function escapeHtml(value) {
    return String(value ?? '—').replace(/[&<>'"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
  }

  function formatBytes(value) {
    const bytes = Number(value || 0);
    if (bytes < 1024) return `${bytes} B`;
    const units = ['KB', 'MB', 'GB', 'TB'];
    let n = bytes / 1024, i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
    return `${n.toFixed(n >= 10 ? 0 : 1)} ${units[i]}`;
  }

  function badge(status) {
    const name = String(status || '—');
    return `<span class="badge ${name.toLowerCase()}">${escapeHtml(name.replaceAll('_', ' '))}</span>`;
  }

  function visiblePage(name) {
    scrollToTop();
    $$('.page').forEach(page => page.classList.toggle('active-page', page.id === `${name}-page`));
    $$('.nav-item').forEach(button => button.classList.toggle('active', button.dataset.page === name));
    const titles = { dashboard: ['OPERAÇÃO LOCAL', 'Dashboard'], monitoring: ['ESTUDOS RECEBIDOS', 'Monitoramento'], queue: ['STORE AND FORWARD', 'Fila de transmissão'], dicom: ['RECEPÇÃO DICOM', 'DICOM local'], modalities: ['NÓS REMOTOS', 'Modalidades'], destinations: ['TRANSMISSÃO SEGURA', 'VOXEL Cloud e destinos'], logs: ['OBSERVABILIDADE', 'Logs e auditoria'], settings: ['ADMINISTRAÇÃO', 'Configurações'] };
    $('#page-kicker').textContent = titles[name][0];
    $('#page-title').textContent = titles[name][1];
    ({ dashboard: loadDashboard, monitoring: loadStudies, queue: loadQueue, dicom: loadDicom, modalities: loadModalities, destinations: loadDestinations, logs: loadLogs, settings: loadSettings }[name])?.();
    requestAnimationFrame(scrollToTop);
  }

  async function verifyBootstrap() {
    const bootstrap = await api('/api/auth/bootstrap-status');
    $('#provision-form').hidden = bootstrap.provisioned;
    $('#auth-form').hidden = !bootstrap.provisioned;
    scrollToTop();
    if (bootstrap.provisioned) $('#username').focus();
  }

  function replacePath(path) {
    if (location.pathname !== path) history.replaceState(null, '', path);
  }

  async function initialize() {
    try {
      state.user = await api('/api/auth/me');
      if (state.user.must_change_password) {
        replacePath('/');
        scrollToTop();
        $('#password-dialog').showModal();
      } else {
        showApp();
      }
    } catch (_) {
      // URLs administrativas não autenticadas retornam explicitamente à tela de login.
      replacePath('/');
      await verifyBootstrap();
    }
  }

  function showApp() {
    $('#login-message').textContent = '';
    $('#login-view').hidden = true;
    $('#app-view').hidden = false;
    replacePath('/dashboard');
    scrollToTop();
    visiblePage('dashboard');
    window.setInterval(() => { $('#clock').textContent = new Date().toLocaleString('pt-BR'); }, 1000);
  }

  async function loadDashboard() {
    try {
      state.system = await api('/api/system');
      const { health, queue } = state.system;
      $('#router-id').textContent = `Router ID: ${state.system.router_id}`;
      $('#router-status').textContent = `ROUTER ${health.router.status}`;
      const services = [['ROUTER', health.router.status], ['ORTHANC', health.orthanc.status], ['VOXEL CLOUD', health.cloud.status], ['DICOM SCP', health.dicom.status]];
      $('#health-cards').innerHTML = services.map(([name, value]) => `<article class="health-card ${/OFFLINE|ERROR|DISCONNECTED/.test(value) ? 'error' : /WARNING/.test(value) ? 'warning' : ''}"><p>${name}</p><strong><span class="status-dot ${/ONLINE|CONNECTED|LISTENING/.test(value) ? 'online' : ''}"></span> ${escapeHtml(value)}</strong></article>`).join('');
      const metrics = [['RECEBIDOS', queue.received], ['PENDENTES', queue.pending], ['ENVIANDO', queue.sending], ['ENVIADOS', queue.sent], ['ERROS', queue.errors], ['AGUARDANDO RETRY', queue.retry]];
      $('#metric-cards').innerHTML = metrics.map(([name, value]) => `<article class="metric-card"><p>${name}</p><strong>${Number(value).toLocaleString('pt-BR')}</strong></article>`).join('');
      $('#network-health').innerHTML = Object.entries(health.network).map(([name, value]) => `<div class="health-row"><span>${escapeHtml(name.replaceAll('_', ' ').toUpperCase())}</span><span class="${value !== 'OK' ? 'offline' : ''}">● ${escapeHtml(value)}</span></div>`).join('');
      const storage = health.storage;
      $('#storage-progress').style.width = `${storage.percent}%`;
      $('#storage-value').textContent = `${storage.percent.toFixed(1)}%`;
      $('#storage-detail').textContent = `${formatBytes(storage.used_bytes)} utilizados de ${formatBytes(storage.total_bytes)} · ${formatBytes(storage.free_bytes)} disponíveis`;
      $('#storage-status').textContent = storage.status;
      $('#storage-status').className = `badge ${storage.status.toLowerCase()}`;
    } catch (error) { console.error(error); }
  }

  async function loadStudies() {
    const rows = await api('/api/studies');
    $('#studies-table').innerHTML = rows.length ? rows.map(study => `<tr><td>${escapeHtml(study.received_at)}</td><td>${escapeHtml(study.patient_id || 'Sem ID')}<br><small>${study.patient_name ? 'Nome disponível localmente' : ''}</small></td><td>${escapeHtml(study.modalities_in_study)}</td><td title="${escapeHtml(study.study_instance_uid)}">${escapeHtml(study.study_instance_uid).slice(0, 22)}…</td><td>${formatBytes(study.total_bytes)}</td><td>${badge(study.status)}</td><td>${study.status === 'VALIDATED' ? '100%' : '—'}</td></tr>`).join('') : '<tr><td colspan="7" class="empty-cell">Nenhum estudo recebido.</td></tr>';
  }

  async function loadQueue() {
    const rows = await api('/api/queue');
    $('#queue-table').innerHTML = rows.length ? rows.map(item => `<tr><td>${labels[item.priority]}</td><td title="${escapeHtml(item.study_instance_uid)}">${escapeHtml(item.study_instance_uid).slice(0, 24)}…</td><td>${escapeHtml(item.destination_name)}</td><td>${badge(item.status)}</td><td>${item.attempt_count}</td><td>${escapeHtml(item.next_attempt_at)}</td><td><div class="action-group">${item.status === 'PAUSED' ? `<button class="secondary" data-queue-action="resume" data-id="${item.id}">RETOMAR</button>` : `<button class="secondary" data-queue-action="pause" data-id="${item.id}">PAUSAR</button>`}<button class="secondary" data-queue-action="retry" data-id="${item.id}">REENVIAR</button><button class="secondary" data-queue-action="cancel" data-id="${item.id}">CANCELAR</button></div></td></tr>`).join('') : '<tr><td colspan="7" class="empty-cell">A fila está vazia.</td></tr>';
  }

  async function loadDicom() {
    const config = await api('/api/settings/dicom');
    const health = await api('/api/health/dicom');
    $('#dicom-ae').textContent = config.ae_title;
    $('#dicom-port').textContent = config.port;
    $('#dicom-state').innerHTML = badge(health.status);
    $('#dicom-copy').textContent = `AE Title: ${config.ae_title}\nIP: ${location.hostname}\nPorta: ${config.port}`;
  }

  async function loadModalities() {
    const rows = await api('/api/modalities');
    $('#modalities-table').innerHTML = rows.length ? rows.map(row => `<tr><td>${escapeHtml(row.name)}</td><td>${escapeHtml(row.ae_title)}</td><td>${escapeHtml(row.host)}:${row.port}</td><td>${escapeHtml(row.modality)}</td><td>${escapeHtml(row.location)}</td><td>${badge(row.enabled ? 'ONLINE' : 'PAUSED')}</td></tr>`).join('') : '<tr><td colspan="6" class="empty-cell">Nenhuma modalidade cadastrada.</td></tr>';
  }

  async function loadDestinations() {
    const rows = await api('/api/destinations');
    $('#destinations-table').innerHTML = rows.length ? rows.map(row => `<tr><td>${escapeHtml(row.name)}</td><td>${escapeHtml(row.kind)}</td><td>${escapeHtml(row.kind === 'DICOM' ? `${row.ae_title}@${row.host}:${row.port}` : row.endpoint)}</td><td>${row.tls_enabled ? badge('TLS') : '—'}</td><td>${labels[row.priority]}</td><td>${badge(row.enabled ? 'ONLINE' : 'PAUSED')}</td></tr>`).join('') : '<tr><td colspan="6" class="empty-cell">Nenhum destino configurado. Estudos permanecerão em armazenamento local até o provisionamento.</td></tr>';
  }

  async function loadLogs() {
    const [logs, audit] = await Promise.all([api('/api/logs'), api('/api/audit')]);
    $('#logs-table').innerHTML = logs.length ? logs.map(row => `<tr><td>${escapeHtml(row.created_at)}</td><td>${escapeHtml(row.category)}</td><td>${badge(row.severity)}</td><td>${escapeHtml(row.code)}</td><td>${escapeHtml(row.message)}</td></tr>`).join('') : '<tr><td colspan="5" class="empty-cell">Nenhum evento registrado.</td></tr>';
    $('#audit-table').innerHTML = audit.length ? audit.map(row => `<tr><td>${escapeHtml(row.created_at)}</td><td>${escapeHtml(row.action)}</td><td>${escapeHtml(row.entity_type)}</td><td>${badge(row.result)}</td></tr>`).join('') : '<tr><td colspan="4" class="empty-cell">Nenhum evento de auditoria.</td></tr>';
  }

  async function loadSettings() {
    const [system, storage] = await Promise.all([api('/api/settings/system'), api('/api/settings/storage')]);
    const sys = $('#settings-system'); const st = $('#settings-storage');
    sys.elements.equipment_name.value = system.equipment_name || '';
    sys.elements.timezone.value = system.timezone || 'America/Sao_Paulo';
    st.elements.retention_hours.value = storage.retention_hours || 168;
    st.elements.auto_delete.checked = Boolean(storage.auto_delete);
  }

  function openModal(title, fields, save) {
    const dialog = $('#modal');
    $('#modal-title').textContent = title;
    $('#modal-fields').innerHTML = fields;
    const form = $('#modal-form');
    form.onsubmit = async (event) => {
      event.preventDefault();
      const values = Object.fromEntries(new FormData(form));
      $$('#modal-fields input[type="checkbox"]').forEach(input => values[input.name] = input.checked);
      try { await save(values); dialog.close(); } catch (error) { alert(error.message); }
    };
    dialog.showModal();
  }

  function bindEvents() {
    $('#form-provision').addEventListener('submit', async (event) => {
      event.preventDefault();
      const password = $('#provision-password').value;
      if (password !== $('#provision-confirm').value) return showMessage('As senhas informadas não coincidem.');
      try {
        await api('/api/auth/provision', { method: 'POST', body: JSON.stringify({ username: $('#provision-username').value, password }) });
        showMessage('Administrador provisionado. Faça o primeiro login.', false);
        await verifyBootstrap();
        scrollToTop();
      } catch (error) { showMessage(error.message); }
    });
    $('#form-login').addEventListener('submit', async (event) => {
      event.preventDefault();
      try { state.user = await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ username: $('#username').value, password: $('#password').value }) }); if (state.user.must_change_password) $('#password-dialog').showModal(); else showApp(); } catch (error) { showMessage('Usuário ou senha inválidos.'); }
    });
    $('#toggle-password').addEventListener('click', () => { const field = $('#password'); field.type = field.type === 'password' ? 'text' : 'password'; $('#toggle-password').textContent = field.type === 'password' ? 'Mostrar' : 'Ocultar'; });
    $('#force-password-form').addEventListener('submit', async (event) => {
      event.preventDefault(); const form = new FormData(event.target);
      try { await api('/api/auth/change-password', { method: 'POST', body: JSON.stringify(Object.fromEntries(form)) }); $('#password-dialog').close(); showMessage('Senha alterada. Entre novamente para continuar.', false); location.reload(); } catch (error) { $('#force-password-message').textContent = error.message; }
    });
    $$('.nav-item').forEach(button => button.addEventListener('click', () => visiblePage(button.dataset.page)));
    $('#refresh-dashboard').addEventListener('click', loadDashboard);
    $$('.refresh-table').forEach(button => button.addEventListener('click', () => ({studies:loadStudies,queue:loadQueue,logs:loadLogs,audit:loadLogs}[button.dataset.resource])()));
    $('#start-scp').addEventListener('click', () => api('/api/dicom/start', { method: 'POST' }).then(loadDicom).catch(error => alert(error.message)));
    $('#stop-scp').addEventListener('click', () => api('/api/dicom/stop', { method: 'POST' }).then(loadDicom).catch(error => alert(error.message)));
    $('#copy-dicom').addEventListener('click', async () => { await navigator.clipboard.writeText($('#dicom-copy').textContent); $('#copy-dicom').textContent = 'COPIADO'; setTimeout(() => $('#copy-dicom').textContent = 'COPIAR CONFIGURAÇÃO', 1200); });
    $('#queue-table').addEventListener('click', event => { const action = event.target.dataset.queueAction; if (!action) return; api(`/api/queue/${event.target.dataset.id}/${action}`, { method: 'POST' }).then(loadQueue).catch(error => alert(error.message)); });
    $('#add-modality').addEventListener('click', () => openModal('Adicionar modalidade', '<label>Nome<input name="name" required></label><label>Descrição<input name="description"></label><label>AE Title<input name="ae_title" maxlength="16" required></label><label>IP / Host<input name="host" required></label><label>Porta<input name="port" type="number" value="104" min="1" max="65535" required></label><label>Modalidade<input name="modality" placeholder="CT"></label><label>Fabricante<input name="manufacturer"></label><label>Localização<input name="location"></label><label class="checkbox-label"><input name="enabled" type="checkbox" checked> Ativa</label>', async values => { values.port = Number(values.port); await api('/api/modalities', { method: 'POST', body: JSON.stringify(values) }); await loadModalities(); }));
    $('#add-destination').addEventListener('click', () => openModal('Adicionar destino', '<label>Nome<input name="name" required></label><label>Tipo<select name="kind"><option>DICOM</option><option>DICOMWEB</option><option>CLOUD</option></select></label><label>AE Title (DICOM)<input name="ae_title" maxlength="16"></label><label>IP / Host (DICOM)<input name="host"></label><label>Porta (DICOM)<input name="port" type="number" value="104" min="1" max="65535"></label><label>Endpoint (HTTP/S)<input name="endpoint"></label><label>Prioridade<select name="priority"><option value="1">CRITICAL</option><option value="2">HIGH</option><option value="3" selected>NORMAL</option><option value="4">LOW</option></select></label><label class="checkbox-label"><input name="tls_enabled" type="checkbox"> TLS habilitado</label><label class="checkbox-label"><input name="enabled" type="checkbox" checked> Ativo</label>', async values => { values.port = values.port ? Number(values.port) : null; values.priority = Number(values.priority); await api('/api/destinations', { method: 'POST', body: JSON.stringify(values) }); await loadDestinations(); }));
    $('#settings-system').addEventListener('submit', event => { event.preventDefault(); const form = new FormData(event.target); api('/api/settings/system', { method: 'PATCH', body: JSON.stringify({ values: Object.fromEntries(form) }) }).then(() => alert('Configurações salvas.')).catch(error => alert(error.message)); });
    $('#settings-storage').addEventListener('submit', event => { event.preventDefault(); const form = new FormData(event.target); api('/api/settings/storage', { method: 'PATCH', body: JSON.stringify({ values: { retention_hours: Number(form.get('retention_hours')), auto_delete: form.get('auto_delete') === 'on' } }) }).then(() => alert('Política de retenção salva.')).catch(error => alert(error.message)); });
    $('#change-username').addEventListener('submit', event => { event.preventDefault(); api('/api/auth/username', { method: 'PUT', body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }).then(() => alert('Usuário alterado.')).catch(error => alert(error.message)); });
    $('#change-password').addEventListener('submit', event => { event.preventDefault(); api('/api/auth/change-password', { method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }).then(() => { alert('Senha alterada; entre novamente.'); location.reload(); }).catch(error => alert(error.message)); });
    $('#logout').addEventListener('click', () => api('/api/auth/logout', { method: 'POST' }).finally(() => { scrollToTop(); location.replace('/'); }));
  }

  bindEvents(); initialize();
})();
