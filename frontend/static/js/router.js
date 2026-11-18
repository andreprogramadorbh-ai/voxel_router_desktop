(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const AUTH_STATES = Object.freeze({ CHECKING: 'checking', UNAUTHENTICATED: 'unauthenticated', AUTHENTICATED: 'authenticated' });
  const state = { system: null, user: null, auth: AUTH_STATES.CHECKING };
  const labels = { 1: 'CRÍTICA', 2: 'ALTA', 3: 'NORMAL', 4: 'BAIXA' };
  // Códigos internos da API continuam em inglês; a interface mostra sempre o rótulo em português.
  const STATUS_LABELS = {
    ONLINE: 'ATIVO', LISTENING: 'ATIVO', OFFLINE: 'PARADO', STOPPED: 'PARADO', RUNNING: 'EM EXECUÇÃO',
    CONNECTED: 'CONECTADO', DISCONNECTED: 'DESCONECTADO', PAUSED: 'PAUSADO', OK: 'OK',
    WARNING: 'ATENÇÃO', CRITICAL: 'CRÍTICO', EMERGENCY: 'EMERGÊNCIA', ERROR: 'ERRO', INFO: 'INFORMAÇÃO',
    RECEIVED: 'RECEBIDO', VALIDATING: 'VALIDANDO', PROCESSING: 'PROCESSANDO', READY_TO_SEND: 'PRONTO PARA ENVIO',
    QUEUED: 'NA FILA', PENDING: 'PENDENTE', SENDING: 'ENVIANDO', SENT: 'ENVIADO', VALIDATED: 'VALIDADO',
    RETRY: 'NOVA TENTATIVA', RETRYING: 'NOVA TENTATIVA', CANCELLED: 'CANCELADO', COMPLETED: 'CONCLUÍDO',
    STARTED: 'INICIADO', SUCCESS: 'SUCESSO', FAILED: 'FALHOU', FAILURE: 'FALHA', DENIED: 'NEGADO',
    NO_TEST: 'SEM TESTE', STAGED: 'PREPARADO', CONFIGURED: 'CONFIGURADO',
  };
  const statusLabel = (code) => STATUS_LABELS[String(code || '')] || String(code || '—').replaceAll('_', ' ');

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
    // Erros de validação (422) chegam como lista: mostra a mensagem de cada campo.
    const detail = Array.isArray(payload.detail) ? payload.detail.map(item => String(item.msg || '').replace(/^Value error, /, '')).join('; ') : payload.detail;
    if (!response.ok) throw new Error(detail || 'Não foi possível concluir a operação.');
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

  // A cor vem do código interno; o texto, do rótulo em português (ou de um rótulo explícito).
  function badge(status, label) {
    const name = String(status || '—');
    return `<span class="badge ${name.toLowerCase()}">${escapeHtml(label || statusLabel(name))}</span>`;
  }

  function visiblePage(name) {
    scrollToTop();
    $$('.page').forEach(page => page.classList.toggle('active-page', page.id === `${name}-page`));
    $$('.nav-item').forEach(button => button.classList.toggle('active', button.dataset.page === name));
    const titles = { dashboard: ['OPERAÇÃO LOCAL', 'Dashboard'], monitoring: ['ESTUDOS RECEBIDOS', 'Monitoramento'], queue: ['STORE AND FORWARD', 'Fila de transmissão'], dicom: ['RECEPÇÃO DICOM', 'DICOM local'], modalities: ['NÓS REMOTOS', 'Modalidades'], destinations: ['TRANSMISSÃO SEGURA', 'VOXEL Cloud e destinos'], 'non-dicom': ['INTEGRAÇÃO', 'Non-DICOM Integration'], logs: ['OBSERVABILIDADE', 'Logs e auditoria'], settings: ['ADMINISTRAÇÃO', 'Configurações'] };
    $('#page-kicker').textContent = titles[name][0];
    $('#page-title').textContent = titles[name][1];
    ({ dashboard: loadDashboard, monitoring: loadStudies, queue: loadQueue, dicom: loadDicom, modalities: loadModalities, destinations: loadDestinations, 'non-dicom': loadNonDicom, logs: loadLogs, settings: loadSettings }[name])?.();
    requestAnimationFrame(scrollToTop);
  }

  function renderAuthChecking() {
    state.auth = AUTH_STATES.CHECKING;
    state.user = null;
    $('#app-view').hidden = true;
    $('#login-view').hidden = false;
    $('#login-view').setAttribute('aria-busy', 'true');
    $('#auth-checking').hidden = false;
    $('#provision-form').hidden = true;
    $('#auth-form').hidden = true;
    scrollToTop();
  }

  function renderAuthScreen(provisioned) {
    state.auth = AUTH_STATES.UNAUTHENTICATED;
    state.user = null;
    $('#app-view').hidden = true;
    $('#login-view').hidden = false;
    $('#login-view').setAttribute('aria-busy', 'false');
    $('#auth-checking').hidden = true;
    $('#provision-form').hidden = provisioned;
    $('#auth-form').hidden = !provisioned;
    scrollToTop();
    if (provisioned) $('#username').focus();
  }

  function renderApplication(user) {
    state.auth = AUTH_STATES.AUTHENTICATED;
    state.user = user;
    $('#login-message').textContent = '';
    $('#login-view').hidden = true;
    $('#app-view').hidden = false;
    replacePath('/dashboard');
    scrollToTop();
    visiblePage('dashboard');
    window.setInterval(() => { $('#clock').textContent = new Date().toLocaleString('pt-BR'); }, 1000);
  }

  async function verifyBootstrap() {
    const bootstrap = await api('/api/auth/bootstrap-status');
    renderAuthScreen(bootstrap.provisioned);
  }

  function replacePath(path) {
    if (location.pathname !== path) history.replaceState(null, '', path);
  }

  async function initialize() {
    renderAuthChecking();
    try {
      const user = await api('/api/auth/me');
      if (user.must_change_password) {
        replacePath('/');
        renderAuthScreen(true);
        $('#password-dialog').showModal();
      } else {
        renderApplication(user);
      }
    } catch (_) {
      // URLs administrativas não autenticadas retornam explicitamente à tela de login.
      replacePath('/');
      try {
        await verifyBootstrap();
      } catch (error) {
        $('#auth-checking').textContent = 'Não foi possível verificar o acesso local. Tente atualizar a página.';
        console.error(error);
      }
    }
  }

  async function loadDashboard() {
    try {
      state.system = await api('/api/system');
      const { health, queue } = state.system;
      $('#router-id').textContent = `Router ID: ${state.system.router_id}`;
      $('#router-status').textContent = `ROUTER ${statusLabel(health.router.status)}`;
      const cloud = health.cloud || {};
      // VOXEL CLOUD: verde só com destino habilitado e sucesso recente (ver HealthMonitor.cloud).
      const services = [['ROUTER', health.router.status], ['ORTHANC', health.orthanc.status], ['VOXEL CLOUD', cloud.status, `Último sucesso: ${formatUtc(cloud.last_success_at)}`, cloud.detail], ['DICOM SCP', health.dicom.status], ['NON-DICOM', health.non_dicom?.service || 'OFFLINE']];
      $('#health-cards').innerHTML = services.map(([name, value, extra, title]) => `<article class="health-card ${/OFFLINE|ERROR|DISCONNECTED/.test(value) ? 'error' : /WARNING/.test(value) ? 'warning' : ''}"${title ? ` title="${escapeHtml(title)}"` : ''}><p>${name}</p><strong><span class="status-dot ${/ONLINE|CONNECTED|LISTENING/.test(value) ? 'online' : ''}"></span> ${escapeHtml(statusLabel(value))}</strong>${extra ? `<small>${escapeHtml(extra)}</small>` : ''}</article>`).join('');
      const nonDicomStats = health.non_dicom?.stats || {};
      const metrics = [['RECEBIDOS', queue.received], ['PENDENTES', queue.pending], ['ENVIANDO', queue.sending], ['ENVIADOS', queue.sent], ['ERROS', queue.errors], ['AGUARDANDO NOVA TENTATIVA', queue.retry], ['NON-DICOM PENDENTES', nonDicomStats.pending || 0], ['NON-DICOM PROCESSANDO', nonDicomStats.processing || 0], ['NON-DICOM FALHOS', nonDicomStats.failed || 0]];
      $('#metric-cards').innerHTML = metrics.map(([name, value]) => `<article class="metric-card"><p>${name}</p><strong>${Number(value).toLocaleString('pt-BR')}</strong></article>`).join('');
      renderBacklog(health.queue);
      $('#access-warning').hidden = health.dicom.enabled_modalities !== 0;
      $('#network-health').innerHTML = Object.entries(health.network).map(([name, value]) => `<div class="health-row"><span>${escapeHtml({ local_network: 'REDE LOCAL', dns: 'DNS', internet: 'INTERNET' }[name] || name.replaceAll('_', ' ').toUpperCase())}</span><span class="${value !== 'OK' ? 'offline' : ''}">● ${escapeHtml(statusLabel(value))}</span></div>`).join('');
      const storage = health.storage;
      $('#storage-progress').style.width = `${storage.percent}%`;
      $('#storage-value').textContent = `${storage.percent.toFixed(1)}%`;
      $('#storage-detail').textContent = `${formatBytes(storage.used_bytes)} utilizados de ${formatBytes(storage.total_bytes)} · ${formatBytes(storage.free_bytes)} disponíveis`;
      $('#storage-status').textContent = statusLabel(storage.status);
      $('#storage-status').className = `badge ${storage.status.toLowerCase()}`;
    } catch (error) { console.error(error); }
  }

  // Datas do SQLite chegam em UTC ("AAAA-MM-DD HH:MM:SS"); exibe no fuso local.
  function formatUtc(value) {
    return value ? new Date(`${value.replace(' ', 'T')}Z`).toLocaleString('pt-BR') : 'nenhum';
  }

  function renderBacklog(backlog) {
    const target = $('#queue-backlog');
    if (!backlog) { target.textContent = '—'; target.classList.remove('alert'); return; }
    const pending = backlog.pending_count
      ? `${backlog.pending_count} pendente(s); o mais antigo aguarda há ${backlog.oldest_pending_minutes} min`
      : 'Nenhum envio pendente';
    const paused = backlog.paused_count ? ` · ${backlog.paused_count} pausado(s) pelo operador` : '';
    const alerts = backlog.alerts || [];
    // Mensagens vêm do backend; textContent evita interpretar HTML.
    const warning = alerts.map(alert => `ATENÇÃO: ${alert.message} (${alert.count} item(ns)) · `).join('');
    target.textContent = `${warning}${pending}${paused} · Último envio com sucesso: ${formatUtc(backlog.last_success_at)}`;
    target.classList.toggle('alert', alerts.length > 0);
  }

  async function loadStudies() {
    const rows = await api('/api/studies');
    $('#studies-table').innerHTML = rows.length ? rows.map(study => `<tr><td>${escapeHtml(study.received_at)}</td><td>${escapeHtml(study.patient_id || 'Sem ID')}<br><small>${study.patient_name ? 'Nome disponível localmente' : ''}</small></td><td>${escapeHtml(study.modalities_in_study)}</td><td title="${escapeHtml(study.study_instance_uid)}">${escapeHtml(study.study_instance_uid).slice(0, 22)}…</td><td>${formatBytes(study.total_bytes)}</td><td>${badge(study.status)}</td><td>${study.status === 'VALIDATED' ? '100%' : '—'}</td></tr>`).join('') : '<tr><td colspan="7" class="empty-cell">Nenhum estudo recebido.</td></tr>';
  }

  // Item enviado/validado ou cancelado não tem PAUSAR/RETOMAR: retomar reenviaria o estudo (a API também recusa).
  function queuePauseButton(item) {
    if (item.status === 'PAUSED') return `<button class="secondary" data-queue-action="resume" data-id="${item.id}">RETOMAR</button>`;
    if (['SENT', 'CANCELLED'].includes(item.status) || item.study_status === 'VALIDATED') return '';
    return `<button class="secondary" data-queue-action="pause" data-id="${item.id}">PAUSAR</button>`;
  }

  async function loadQueue() {
    const rows = await api('/api/queue');
    $('#queue-table').innerHTML = rows.length ? rows.map(item => `<tr><td>${labels[item.priority]}</td><td title="${escapeHtml(item.study_instance_uid)}">${escapeHtml(item.study_instance_uid).slice(0, 24)}…</td><td>${escapeHtml(item.destination_name)}</td><td>${badge(item.status)}</td><td>${item.attempt_count}</td><td>${escapeHtml(item.next_attempt_at)}</td><td><div class="action-group">${queuePauseButton(item)}<button class="secondary" data-queue-action="retry" data-id="${item.id}">REENVIAR</button><button class="secondary" data-queue-action="cancel" data-id="${item.id}">CANCELAR</button></div></td></tr>`).join('') : '<tr><td colspan="7" class="empty-cell">A fila está vazia.</td></tr>';
  }

  async function loadNonDicom() {
    try {
      const [status, config, queue, history] = await Promise.all([api('/api/non-dicom/status'), api('/api/non-dicom/config'), api('/api/non-dicom/queue'), api('/api/non-dicom/history')]);
      const values = [['SERVIÇO', status.service], ['DIRETÓRIO', status.directory], ['VOXEL PACS', status.voxel_pacs], ['PROCESSADOR', status.processor]];
      $('#non-dicom-health').innerHTML = values.map(([name, value]) => `<article class="health-card ${/OFFLINE|ERROR|DISCONNECTED/.test(value) ? 'error' : ''}"><p>${name}</p><strong><span class="status-dot ${/ONLINE|OK|CONFIGURED/.test(value) ? 'online' : ''}"></span> ${escapeHtml(statusLabel(value))}</strong></article>`).join('');
      const stats = status.stats || {};
      $('#non-dicom-metrics').innerHTML = [['RECEBIDOS', stats.received], ['PENDENTES', stats.pending], ['PROCESSANDO', stats.processing], ['CONCLUÍDOS', stats.completed], ['FALHOS', stats.failed], ['NOVA TENTATIVA', stats.retry]].map(([name, value]) => `<article class="metric-card"><p>${name}</p><strong>${Number(value || 0).toLocaleString('pt-BR')}</strong></article>`).join('');
      $('#non-dicom-details').innerHTML = [['Diretório raiz', status.directory === 'OK' ? config.root_path || 'Padrão ProgramData' : 'Erro de diretório'], ['Última sincronização', status.last_synchronization || '—'], ['Último arquivo recebido', status.last_file_received || '—'], ['Último processamento', status.last_processing || '—']].map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('');
      const form = $('#non-dicom-config');
      for (const [key, value] of Object.entries(config)) { if (form.elements[key] && key !== 'voxel_pacs_token') form.elements[key].type === 'checkbox' ? form.elements[key].checked = Boolean(value) : form.elements[key].value = value ?? ''; }
      $('#non-dicom-queue-table').innerHTML = queue.length ? queue.map(row => `<tr><td>${badge(row.status)}</td><td>${escapeHtml(row.patient_id)}</td><td>${escapeHtml(row.accession_number)}</td><td>${escapeHtml(row.file_name)}</td><td>${escapeHtml(row.modality)}</td><td>${row.attempt_count}</td><td>${escapeHtml(row.last_error || '—')}</td><td><div class="action-group"><button class="secondary" data-non-dicom-xml="${escapeHtml(row.id)}">VER XML</button><button class="secondary" data-non-dicom-folder="${escapeHtml(row.id)}">ABRIR PASTA</button><button class="secondary" data-non-dicom-retry="${escapeHtml(row.id)}">REPROCESSAR</button></div></td></tr>`).join('') : '<tr><td colspan="8" class="empty-cell">Nenhuma tarefa pendente.</td></tr>';
      $('#non-dicom-history-table').innerHTML = history.length ? history.map(row => `<tr><td>${badge(row.status)}</td><td>${escapeHtml(row.accession_number)}</td><td>${escapeHtml(row.file_name)}</td><td>${escapeHtml(row.completed_at || '—')}</td></tr>`).join('') : '<tr><td colspan="4" class="empty-cell">Nenhum processamento concluído.</td></tr>';
    } catch (error) { console.error(error); }
  }

  async function loadDicom() {
    const [config, health] = await Promise.all([api('/api/settings/dicom'), api('/api/dicom')]);
    // Nunca mostra 0.0.0.0: com escuta em todas as interfaces, lista os IPv4 da LAN (sem loopback, APIPA e VPN).
    const ips = health.bind_host && health.bind_host !== '0.0.0.0' ? [health.bind_host] : (health.local_ipv4 || []);
    $('#dicom-ae').textContent = config.ae_title;
    $('#dicom-port').textContent = config.port;
    $('#dicom-ips').innerHTML = ips.length ? ips.map(escapeHtml).join('<br>') : 'Não identificado — verifique a conexão de rede';
    $('#dicom-state').innerHTML = badge(health.status);
    const ipLines = ips.length ? ips.map(ip => `IP: ${ip}`).join('\n') : 'IP: não identificado (verifique a rede)';
    $('#dicom-copy').textContent = `AE Title: ${config.ae_title}\n${ipLines}\nPorta: ${config.port}`;
    $('#outbound-ae-form').elements.outbound_ae_title.value = config.outbound_ae_title || '';
  }

  const attemptReasons = { UNKNOWN_NODE: 'Não cadastrada', ECHO_ONLY: 'Só C-ECHO', CALLED_AE: 'Called AE incorreto' };

  async function loadModalities() {
    const [rows, access] = await Promise.all([api('/api/modalities'), api('/api/dicom/access')]);
    state.modalities = rows;
    $('#modalities-warning').hidden = access.nodes.enabled > 0;
    $('#modalities-table').innerHTML = rows.length ? rows.map(row => `<tr><td>${escapeHtml(row.name)}</td><td>${escapeHtml(row.ae_title)}</td><td>${escapeHtml(row.host)}</td><td>${escapeHtml(row.modality)}</td><td>${escapeHtml(row.location)}</td><td>${badge(row.enabled ? 'ONLINE' : 'PAUSED', row.enabled ? 'HABILITADA' : 'DESABILITADA')}</td><td><div class="action-group"><button class="secondary" data-node-toggle="${row.id}">${row.enabled ? 'DESABILITAR' : 'HABILITAR'}</button><button class="secondary" data-node-delete="${row.id}">EXCLUIR</button></div></td></tr>`).join('') : '<tr><td colspan="7" class="empty-cell">Nenhuma modalidade cadastrada.</td></tr>';
    const until = access.learning_mode_until;
    $('#learning-status').textContent = until ? `LIGADO ATÉ ${formatUtc(until)}` : 'DESLIGADO';
    $('#learning-status').className = `badge ${until ? 'warning' : 'neutral'}`;
    $('#attempts-table').innerHTML = access.attempts.length ? access.attempts.map(row => `<tr><td>${escapeHtml(row.calling_ae)}</td><td>${escapeHtml(row.source_ip)}</td><td>${escapeHtml(row.called_ae)}</td><td>${escapeHtml(attemptReasons[row.reason] || row.reason)}</td><td>${row.attempt_count}</td><td>${escapeHtml(formatUtc(row.last_seen_at))}</td><td><div class="action-group"><button class="primary" data-attempt-register="${row.id}" data-ae="${escapeHtml(row.calling_ae)}" data-ip="${escapeHtml(row.source_ip)}">CADASTRAR</button><button class="secondary" data-attempt-discard="${row.id}">DESCARTAR</button></div></td></tr>`).join('') : `<tr><td colspan="7" class="empty-cell">${until ? 'Aguardando tentativas de envio. Peça para a modalidade enviar um exame de teste.' : 'Nenhuma recusa registrada.'}</td></tr>`;
  }

  async function onModalitiesClick(event) {
    const data = event.target.dataset;
    try {
      if (data.nodeToggle) {
        const row = state.modalities.find(item => String(item.id) === data.nodeToggle);
        const payload = { name: row.name, description: row.description, ae_title: row.ae_title, host: row.host, port: row.port, modality: row.modality, manufacturer: row.manufacturer, location: row.location, enabled: !row.enabled };
        await api(`/api/modalities/${row.id}`, { method: 'PUT', body: JSON.stringify(payload) });
      } else if (data.nodeDelete) {
        if (!confirm('Excluir esta modalidade? Ela deixará de poder enviar exames.')) return;
        await api(`/api/modalities/${data.nodeDelete}`, { method: 'DELETE' });
      } else if (data.attemptDiscard) {
        await api(`/api/dicom/attempts/${data.attemptDiscard}`, { method: 'DELETE' });
      } else if (data.attemptRegister) {
        openModal(`Cadastrar ${data.ae} (${data.ip})`, '<p>A modalidade passará a poder enviar exames a partir deste IP.</p><label>Nome<input name="name" required></label><label>Modalidade<input name="modality" placeholder="CT"></label><label>Localização<input name="location"></label>', async values => {
          await api(`/api/dicom/attempts/${data.attemptRegister}/register`, { method: 'POST', body: JSON.stringify(values) });
          await loadModalities();
        });
        return;
      } else {
        return;
      }
      await loadModalities();
    } catch (error) { alert(error.message); }
  }

  const echoErrors = { DESTINATION_REJECTED: 'recusado pelo destino (verifique o AE de saída cadastrado na célula)', DESTINATION_UNAVAILABLE: 'sem resposta (rede/VPN)', DESTINATION_ECHO_FAILED: 'resposta com falha', OUTBOUND_AE_NOT_CONFIGURED: 'AE de saída não configurado' };

  function echoCell(row) {
    if (row.kind !== 'DICOM') return '—';
    if (!row.echo_status) return '<span class="badge neutral">NUNCA TESTADO</span>';
    const when = escapeHtml(formatUtc(row.echo_at));
    if (row.echo_status === 'SUCCESS') return row.echo_valid ? `<span class="badge online">SUCESSO</span> ${when}` : `<span class="badge warning">REFAZER</span> ${when}<br><small>configuração mudou: teste de novo</small>`;
    return `<span class="badge error">FALHOU</span> ${when}<br><small>${escapeHtml(echoErrors[row.echo_error] || row.echo_error || '')}</small>`;
  }

  // Tipos não DICOM (DICOMWEB/CLOUD) não têm envio implementado: só podem ser excluídos.
  function destinationButtons(row) {
    const echo = `<button class="secondary" data-destination-echo="${row.id}">TESTAR C-ECHO</button>`;
    return echo + (row.enabled ? `<button class="secondary" data-destination-pause="${row.id}">PAUSAR</button>` : `<button class="primary" data-destination-enable="${row.id}" ${row.echo_valid ? '' : 'disabled title="Execute TESTAR C-ECHO com sucesso antes de habilitar"'}>HABILITAR</button>`);
  }

  async function loadDestinations() {
    const rows = await api('/api/destinations');
    state.destinations = rows;
    $('#destinations-table').innerHTML = rows.length ? rows.map(row => `<tr><td>${escapeHtml(row.name)}</td><td>${escapeHtml(row.kind)}</td><td>${escapeHtml(row.kind === 'DICOM' ? `${row.ae_title}@${row.host}:${row.port}` : row.endpoint)}${row.kind === 'DICOM' ? `<br><small>AE de saída: ${escapeHtml(row.outbound_ae_title || 'não configurado')}</small>` : ''}</td><td>${row.tls_enabled ? badge('TLS') : '—'}</td><td>${labels[row.priority]}</td><td>${echoCell(row)}</td><td>${row.kind === 'DICOM' ? badge(row.enabled ? 'ONLINE' : 'PAUSED') : badge('NEUTRAL', 'NÃO SUPORTADO')}</td><td><div class="action-group">${row.kind === 'DICOM' ? destinationButtons(row) : ''}<button class="secondary" data-destination-delete="${row.id}">EXCLUIR</button></div></td></tr>`).join('') : '<tr><td colspan="8" class="empty-cell">Nenhum destino configurado. Estudos permanecerão em armazenamento local até o provisionamento.</td></tr>';
  }

  async function onDestinationsClick(event) {
    const button = event.target.closest('button');
    if (!button) return;
    const data = button.dataset;
    try {
      if (data.destinationEcho) {
        button.disabled = true;
        button.textContent = 'TESTANDO…';
        const result = await api(`/api/destinations/${data.destinationEcho}/echo`, { method: 'POST' });
        if (result.echo_status !== 'SUCCESS') alert(`C-ECHO falhou: ${echoErrors[result.echo_error] || result.echo_error}`);
      } else if (data.destinationEnable) {
        await api(`/api/destinations/${data.destinationEnable}/enable`, { method: 'POST' });
      } else if (data.destinationPause) {
        await api(`/api/destinations/${data.destinationPause}/pause`, { method: 'POST' });
      } else if (data.destinationDelete) {
        if (!confirm('Excluir este destino?')) return;
        await api(`/api/destinations/${data.destinationDelete}`, { method: 'DELETE' });
      } else {
        return;
      }
    } catch (error) {
      alert(error.message);
    }
    await loadDestinations();
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
      try {
        await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ username: $('#username').value, password: $('#password').value }) });
        const user = await api('/api/auth/me');
        if (user.must_change_password) {
          renderAuthScreen(true);
          $('#password-dialog').showModal();
        } else {
          renderApplication(user);
        }
      } catch (error) {
        renderAuthScreen(true);
        showMessage('Usuário ou senha inválidos.');
      }
    });
    $('#toggle-password').addEventListener('click', () => { const field = $('#password'); field.type = field.type === 'password' ? 'text' : 'password'; $('#toggle-password').textContent = field.type === 'password' ? 'Mostrar' : 'Ocultar'; });
    $('#force-password-form').addEventListener('submit', async (event) => {
      event.preventDefault(); const form = new FormData(event.target);
      try {
        await api('/api/auth/change-password', { method: 'POST', body: JSON.stringify(Object.fromEntries(form)) });
        $('#password-dialog').close();
        replacePath('/');
        renderAuthScreen(true);
        showMessage('Senha alterada. Entre novamente para continuar.', false);
      } catch (error) { $('#force-password-message').textContent = error.message; }
    });
    $$('.nav-item').forEach(button => button.addEventListener('click', () => visiblePage(button.dataset.page)));
    $('#refresh-dashboard').addEventListener('click', loadDashboard);
    $('#non-dicom-refresh').addEventListener('click', loadNonDicom);
    $('#non-dicom-test').addEventListener('click', () => api('/api/non-dicom/test', { method: 'POST' }).then(result => { alert(`Diretório: ${result.directory}\nVOXEL PACS: ${result.connection.status}`); loadNonDicom(); }).catch(error => alert(error.message)));
    $('#non-dicom-manual-test').addEventListener('click', () => { if (!confirm('Buscar um único teste manual já preparado? Esta ação não processa a fila nem inicia o processador.')) return; api('/api/non-dicom/manual-test', { method: 'POST', body: JSON.stringify({ confirmation: 'single-manual-test' }) }).then(result => { alert(result.detail || result.status); loadNonDicom(); }).catch(error => alert(error.message)); });
    $('#non-dicom-test-directory').addEventListener('click', () => api('/api/non-dicom/test', { method: 'POST' }).then(result => alert(`Diretório: ${result.directory}\n${result.root_path}`)).catch(error => alert(error.message)));
    $('#non-dicom-process').addEventListener('click', () => api('/api/non-dicom/process', { method: 'POST' }).then(loadNonDicom).catch(error => alert(error.message)));
    $('#non-dicom-retry-all').addEventListener('click', () => { if (confirm('Reprocessar todas as tarefas Non-DICOM que falharam?')) api('/api/non-dicom/retry-all', { method: 'POST' }).then(loadNonDicom).catch(error => alert(error.message)); });
    $('#non-dicom-queue-table').addEventListener('click', event => { const target = event.target; const submissionId = target.dataset.nonDicomRetry; if (submissionId) api(`/api/non-dicom/retry/${submissionId}`, { method: 'POST' }).then(loadNonDicom).catch(error => alert(error.message)); if (target.dataset.nonDicomXml) api(`/api/non-dicom/${target.dataset.nonDicomXml}/xml`).then(xml => alert(xml)).catch(error => alert(error.message)); if (target.dataset.nonDicomFolder) api(`/api/non-dicom/${target.dataset.nonDicomFolder}/open-folder`, { method: 'POST' }).catch(error => alert(error.message)); });
    $('#non-dicom-config').addEventListener('submit', event => { event.preventDefault(); const form = new FormData(event.target); const token = String(form.get('voxel_pacs_token') || ''); const values = Object.fromEntries(form); delete values.voxel_pacs_token; ['polling_interval_seconds', 'max_attempts', 'max_file_size_mb', 'timeout_seconds'].forEach(key => values[key] = Number(values[key])); values.delete_file_after_success = form.get('delete_file_after_success') === 'on'; api('/api/non-dicom/config', { method: 'POST', body: JSON.stringify({ values, ...(token ? { voxel_pacs_token: token } : {}) }) }).then(() => { event.target.elements.voxel_pacs_token.value = ''; alert('Configuração Non-DICOM salva.'); loadNonDicom(); }).catch(error => alert(error.message)); });
    $$('.refresh-table').forEach(button => button.addEventListener('click', () => ({studies:loadStudies,queue:loadQueue,logs:loadLogs,audit:loadLogs}[button.dataset.resource])()));
    $('#start-scp').addEventListener('click', () => api('/api/dicom/start', { method: 'POST' }).then(loadDicom).catch(error => alert(error.message)));
    $('#stop-scp').addEventListener('click', () => api('/api/dicom/stop', { method: 'POST' }).then(loadDicom).catch(error => alert(error.message)));
    $('#copy-dicom').addEventListener('click', async () => { await navigator.clipboard.writeText($('#dicom-copy').textContent); $('#copy-dicom').textContent = 'COPIADO'; setTimeout(() => $('#copy-dicom').textContent = 'COPIAR CONFIGURAÇÃO', 1200); });
    $('#queue-table').addEventListener('click', event => { const action = event.target.dataset.queueAction; if (!action) return; api(`/api/queue/${event.target.dataset.id}/${action}`, { method: 'POST' }).then(loadQueue).catch(error => alert(error.message)); });
    $('#modalities-table').addEventListener('click', onModalitiesClick);
    $('#attempts-table').addEventListener('click', onModalitiesClick);
    $('#destinations-table').addEventListener('click', onDestinationsClick);
    $('#outbound-ae-form').addEventListener('submit', event => { event.preventDefault(); const value = new FormData(event.target).get('outbound_ae_title'); api('/api/settings/dicom', { method: 'PATCH', body: JSON.stringify({ values: { outbound_ae_title: value } }) }).then(() => { alert('AE de saída salvo. Destinos DICOM foram pausados se o valor mudou: teste o C-ECHO de novo.'); loadDicom(); }).catch(error => alert(error.message)); });
    $('#learning-enable').addEventListener('click', () => api('/api/dicom/learning', { method: 'POST', body: JSON.stringify({ hours: Number($('#learning-hours').value) }) }).then(loadModalities).catch(error => alert(error.message)));
    $('#learning-disable').addEventListener('click', () => api('/api/dicom/learning', { method: 'DELETE' }).then(loadModalities).catch(error => alert(error.message)));
    $('#add-modality').addEventListener('click',() => openModal('Adicionar modalidade', '<label>Nome<input name="name" required></label><label>Descrição<input name="description"></label><label>AE Title<input name="ae_title" maxlength="16" required></label><label>IP ou faixa CIDR<input name="host" placeholder="192.168.0.50 ou 192.168.0.0/24" required></label><label>Porta<input name="port" type="number" value="104" min="1" max="65535" required></label><label>Modalidade<input name="modality" placeholder="CT"></label><label>Fabricante<input name="manufacturer"></label><label>Localização<input name="location"></label><label class="checkbox-label"><input name="enabled" type="checkbox" checked> Ativa</label>', async values => { values.port = Number(values.port); await api('/api/modalities', { method: 'POST', body: JSON.stringify(values) }); await loadModalities(); }));
    $('#add-destination').addEventListener('click', () => openModal('Adicionar destino DICOM', '<label>Nome<input name="name" required></label><label>AE Title do destino<input name="ae_title" maxlength="16" required></label><label>IP / Host<input name="host" required></label><label>Porta<input name="port" type="number" value="104" min="1" max="65535" required></label><label>Prioridade<select name="priority"><option value="1">CRÍTICA</option><option value="2">ALTA</option><option value="3" selected>NORMAL</option><option value="4">BAIXA</option></select></label><label class="checkbox-label"><input name="tls_enabled" type="checkbox"> TLS habilitado</label><p class="helper">Só destinos DICOM (C-STORE) são suportados. O destino será criado PAUSADO; depois, use TESTAR C-ECHO e HABILITAR.</p>', async values => { values.kind = 'DICOM'; values.tls_enabled = values.tls_enabled === 'on'; values.port = Number(values.port); values.priority = Number(values.priority); await api('/api/destinations', { method: 'POST', body: JSON.stringify(values) }); await loadDestinations(); }));
    $('#settings-system').addEventListener('submit', event => { event.preventDefault(); const form = new FormData(event.target); api('/api/settings/system', { method: 'PATCH', body: JSON.stringify({ values: Object.fromEntries(form) }) }).then(() => alert('Configurações salvas.')).catch(error => alert(error.message)); });
    $('#settings-storage').addEventListener('submit', event => { event.preventDefault(); const form = new FormData(event.target); api('/api/settings/storage', { method: 'PATCH', body: JSON.stringify({ values: { retention_hours: Number(form.get('retention_hours')), auto_delete: form.get('auto_delete') === 'on' } }) }).then(() => alert('Política de retenção salva.')).catch(error => alert(error.message)); });
    $('#change-username').addEventListener('submit', event => { event.preventDefault(); api('/api/auth/username', { method: 'PUT', body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }).then(() => alert('Usuário alterado.')).catch(error => alert(error.message)); });
    $('#change-password').addEventListener('submit', event => { event.preventDefault(); api('/api/auth/change-password', { method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }).then(() => { alert('Senha alterada; entre novamente.'); location.reload(); }).catch(error => alert(error.message)); });
    $('#logout').addEventListener('click', async () => {
      renderAuthChecking();
      try { await api('/api/auth/logout', { method: 'POST' }); }
      finally {
        replacePath('/');
        await verifyBootstrap();
      }
    });
  }

  bindEvents(); initialize();
})();
