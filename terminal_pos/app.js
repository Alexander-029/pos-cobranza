const $ = id => document.getElementById(id);
const money = value => new Intl.NumberFormat('es-PY').format(value || 0);
const localTime = value => new Intl.DateTimeFormat('es-PY', {
  timeZone: 'America/Asuncion', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
}).format(new Date(value));
const views = ['screenLogin', 'screenHome', 'screenMethod', 'screenCardSelect', 'screenCard', 'screenPin', 'screenQr',
  'screenProcessing', 'screenResult', 'screenBatch', 'screenHistory', 'screenTicket'];
const reasons = {
  INSUFFICIENT_FUNDS: 'Saldo ficticio insuficiente.', LIMIT_EXCEEDED: 'Límite de crédito ficticio insuficiente.',
  CARD_BLOCKED: 'La tarjeta de prueba está bloqueada.', PIN_LIMIT: 'Se agotaron los intentos de PIN.',
  UNKNOWN_CARD: 'El emisor simulado no reconoce esta tarjeta.', QR_EXPIRED: 'El QR venció.',
  QR_CANCELLED: 'Se canceló el QR.', CARD_CANCELLED: 'Se canceló la venta con tarjeta.',
  BATCH_CLOSED: 'El lote se cerró.'
};
let cards = [];
let amount = 0;
let batch = null;
let requestId = null;
let activeQr = null;
let qrTimer = null;
let qrTicker = null;
let qrExpiry = 0;
let busy = false;
let currentView = 'screenHome';
let ticketReturn = 'screenHistory';
let lastTicketId = null;
let selectedCardId = null;
let readMethod = null;
let needsChip = false;
let drag = null;
let pinValue = '';
let employee = null;

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.message || result.error || 'El servidor no respondió.');
    error.status = response.status;
    throw error;
  }
  return result;
}

function announce(message) {
  $('result').textContent = message;
  $('screenMessage').textContent = message;
}
function screen(id) {
  currentView = id;
  for (const view of views) $(view).classList.toggle('hidden', view !== id);
  document.querySelector('.device-screen').classList.toggle('is-locked', id === 'screenLogin');
  $('logoutButton').classList.toggle('hidden', !employee || id === 'screenLogin');
  const cardReady = id === 'screenCardSelect' || (id === 'screenCard' && needsChip);
  $('deviceArea').classList.toggle('can-read', cardReady);
  $('cardTray').classList.toggle('is-ready', cardReady);
  $('cardTrayHint').textContent = id === 'screenCard' && needsChip
    ? 'Insertá la misma tarjeta en la ranura inferior.'
    : cardReady ? 'Arrastrá la tarjeta al lector o elegila y tocá el lector.'
      : 'Primero ingresá el monto y elegí Tarjeta en el POS.';
  $('backButton').disabled = busy || !!activeQr || ['screenLogin', 'screenHome', 'screenCard', 'screenPin', 'screenQr', 'screenProcessing'].includes(id);
  for (const [button, view] of [['navHome', 'screenHome'], ['navBatch', 'screenBatch'], ['navHistory', 'screenHistory']]) {
    $(button).classList.toggle('is-active', id === view);
  }
  updateControls();
}
function updateControls() {
  const open = batch && !batch.closed_at;
  $('screenPay').disabled = busy || !open || !!activeQr;
  $('openBatch').disabled = busy || !!open || !!activeQr;
  $('closeBatch').disabled = busy || !open || !!activeQr || batch.operator_id !== employee?.id;
  updatePinDisplay();
  $('cancelQr').disabled = busy;
  $('cancelCard').disabled = busy;
  $('cancelPin').disabled = busy;
  for (const id of ['navHome', 'navBatch', 'navHistory']) $(id).disabled = busy || !!activeQr || !employee;
  $('backButton').disabled = busy || !!activeQr || ['screenLogin', 'screenHome', 'screenCard', 'screenPin', 'screenQr', 'screenProcessing'].includes(currentView);
  $('logoutButton').disabled = busy || !!activeQr;
  $('loginButton').disabled = busy;
}
async function run(task) {
  if (busy) return;
  busy = true;
  updateControls();
  try { await task(); }
  catch (error) { if (error.status === 401) showLogin('La sesión terminó. Ingresá otra vez.'); else showError(error.message); }
  finally { busy = false; updateControls(); }
}
function showError(message) {
  $('resultEmblem').textContent = '!';
  $('resultEmblem').classList.add('is-declined');
  $('resultKicker').textContent = 'NO SE PUDO CONTINUAR';
  $('resultTitle').textContent = 'Revisá la operación';
  $('resultDetail').textContent = message;
  $('resultAmount').textContent = money(amount);
  $('viewTicket').classList.add('hidden');
  screen('screenResult');
  announce(message);
}
function showLogin(message = '') {
  employee = null;
  batch = null;
  stopQrPolling();
  activeQr = null;
  requestId = null;
  clearTicket();
  $('receiptPaper').classList.remove('is-printed', 'is-printing');
  $('operations').replaceChildren();
  $('operatorPin').value = '';
  $('loginMessage').textContent = message;
  screen('screenLogin');
}
async function loadTerminal() {
  cards = await api('/api/cards-demo');
  for (const element of document.querySelectorAll('.wallet-card')) {
    const card = cards.find(item => item.id === element.dataset.card);
    if (card) element.querySelector('b').textContent = `•••• •••• •••• ${card.last4}`;
  }
  updatePlans();
  await refresh();
  screen('screenHome');
  announce(`Sesión iniciada: ${employee.name}.`);
}
function setAmount(value) {
  amount = Number(value);
  $('screenAmount').textContent = Number.isInteger(amount) && amount >= 0 ? money(amount) : '—';
}
function updatePinDisplay() {
  const dots = $('pinDots').children;
  for (let index = 0; index < dots.length; index++) dots[index].classList.toggle('filled', index < pinValue.length);
  $('pinDots').setAttribute('aria-label', `${pinValue.length} de 4 dígitos ingresados`);
  $('submitPin').disabled = busy || pinValue.length !== 4;
}
function updatePlans() {
  const card = cards.find(item => item.id === selectedCardId);
  $('installments').replaceChildren();
  for (const count of card?.installments || [1]) {
    const option = document.createElement('option');
    option.value = count;
    option.textContent = count === 1 ? 'Contado' : `${count} cuotas`;
    $('installments').append(option);
  }
  $('chosenCard').textContent = card ? `${card.type === 'CREDIT' ? 'Crédito' : 'Débito'} · •••• ${card.last4}` : 'Elegí débito o crédito';
  for (const element of document.querySelectorAll('.wallet-card')) {
    element.classList.toggle('is-selected', element.dataset.card === selectedCardId);
  }
}
function clearTicket() {
  $('ticket').textContent = 'Seleccioná una operación aprobada.';
  $('printTicket').disabled = true;
  lastTicketId = null;
}
function stopQrPolling() {
  clearTimeout(qrTimer);
  clearInterval(qrTicker);
  qrTimer = null;
  qrTicker = null;
}
function resetHome() {
  stopQrPolling();
  setAmount(0);
  activeQr = null;
  requestId = null;
  needsChip = false;
  selectedCardId = null;
  updatePlans();
  pinValue = '';
  updatePinDisplay();
  $('receiptPaper').classList.remove('is-printing', 'is-printed');
  screen('screenHome');
  announce('Terminal lista para una venta de prueba.');
}
function selectCard(id) {
  if (!cards.some(card => card.id === id)) return;
  if (selectedCardId === id) return;
  selectedCardId = id;
  updatePlans();
}
function chooseAmount() {
  if (!batch || batch.closed_at) { screen('screenBatch'); announce('Abrí un lote antes de cobrar.'); return; }
  if (!Number.isInteger(amount) || amount < 1 || amount > 100_000_000) {
    announce('Ingresá un importe entero entre 1 y 100.000.000 Gs.');
    return;
  }
  $('methodAmount').textContent = money(amount);
  $('selectAmount').textContent = money(amount);
  screen('screenMethod');
}
function dropMethod(x, y) {
  const top = $('contactlessTarget').getBoundingClientRect();
  if (x >= top.left - 15 && x <= top.right + 15 && y >= top.top - 15 && y <= top.bottom + 15) return 'CONTACTLESS';
  const chip = $('chipTarget').getBoundingClientRect();
  if (x >= chip.left - 70 && x <= chip.right + 70 && y >= chip.top - 75 && y <= chip.bottom + 65) return 'CHIP';
  return null;
}
function markDrop(method) {
  $('deviceArea').classList.toggle('drop-top', method === 'CONTACTLESS');
  $('deviceArea').classList.toggle('drop-bottom', method === 'CHIP');
}
async function animateCardRead(element, method, startRect) {
  const target = $(method === 'CHIP' ? 'chipTarget' : 'contactlessTarget').getBoundingClientRect();
  const x = target.left + target.width / 2 - startRect.left - startRect.width / 2;
  const y = target.top + target.height / 2 - startRect.top - startRect.height / 2;
  element.classList.remove('is-dragging');
  element.classList.add('is-presented');
  element.style.transform = `translate(${x}px,${y}px) rotate(${method === 'CHIP' ? 90 : -8}deg) scale(.72)`;
  $('deviceArea').classList.toggle('is-tapping', method === 'CONTACTLESS');
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
    await new Promise(resolve => setTimeout(resolve, 600));
  }
  element.style.transform = '';
  element.classList.remove('is-presented');
  $('deviceArea').classList.remove('is-tapping');
  markDrop(null);
}
function bindCards() {
  for (const element of document.querySelectorAll('.wallet-card')) {
    element.addEventListener('click', () => {
      if (currentView === 'screenCardSelect' && !busy) selectCard(element.dataset.card);
    });
    element.addEventListener('pointerdown', event => {
      if (busy || activeQr || (currentView !== 'screenCardSelect' && !(currentView === 'screenCard' && needsChip))) return;
      if (currentView === 'screenCard' && element.dataset.card !== selectedCardId) return;
      selectCard(element.dataset.card);
      drag = {element, pointerId: event.pointerId, x: event.clientX, y: event.clientY, rect: element.getBoundingClientRect()};
      element.setPointerCapture(event.pointerId);
      element.classList.add('is-dragging');
      event.preventDefault();
    });
    element.addEventListener('pointermove', event => {
      if (!drag || drag.element !== element || drag.pointerId !== event.pointerId) return;
      element.style.transform = `translate(${event.clientX - drag.x}px,${event.clientY - drag.y}px)`;
      markDrop(dropMethod(event.clientX, event.clientY));
    });
    element.addEventListener('pointerup', event => {
      if (!drag || drag.element !== element || drag.pointerId !== event.pointerId) return;
      const startRect = drag.rect;
      const method = dropMethod(event.clientX, event.clientY);
      drag = null;
      element.releasePointerCapture(event.pointerId);
      markDrop(null);
      if (!method || (currentView === 'screenCard' && method !== 'CHIP')) {
        element.classList.remove('is-dragging');
        element.classList.add('is-returning');
        element.style.transform = '';
        setTimeout(() => element.classList.remove('is-returning'), 560);
        if (currentView === 'screenCard' && needsChip) announce('Insertá la misma tarjeta en la ranura inferior.');
        return;
      }
      if (currentView === 'screenCardSelect') {
        run(async () => {
          await animateCardRead(element, method, startRect);
          await startCard(method);
        });
      } else {
        run(async () => {
          await animateCardRead(element, 'CHIP', startRect);
          readMethod = 'CHIP';
          needsChip = false;
          await processCard();
        });
      }
    });
    element.addEventListener('pointercancel', () => {
      if (drag?.element !== element) return;
      drag = null;
      markDrop(null);
      element.classList.remove('is-dragging');
      element.style.transform = '';
    });
  }
  for (const [id, method] of [['contactlessTarget', 'CONTACTLESS'], ['chipTarget', 'CHIP']]) {
    const target = $(id);
    target.addEventListener('click', () => activateReader(method));
    target.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      activateReader(method);
    });
  }
}
function activateReader(method) {
  if (busy || activeQr || (currentView !== 'screenCardSelect' && !(currentView === 'screenCard' && needsChip))) return;
  if (currentView === 'screenCard' && method !== 'CHIP') return;
  if (!selectedCardId) { announce('Elegí una tarjeta de prueba primero.'); return; }
  const card = [...document.querySelectorAll('.wallet-card')].find(item => item.dataset.card === selectedCardId);
  if (!card) return;
  run(async () => {
    await animateCardRead(card, method, card.getBoundingClientRect());
    if (currentView === 'screenCardSelect') await startCard(method);
    else {
      readMethod = 'CHIP';
      needsChip = false;
      await processCard();
    }
  });
}
async function processCard(pin) {
  if (pin === undefined) {
    screen('screenCard');
    $('cardInstruction').textContent = readMethod === 'CHIP' ? 'Leyendo chip' : 'Leyendo sin contacto';
    $('cardHint').textContent = 'Esperá la respuesta del emisor simulado.';
    $('cardInstructionIcon').textContent = readMethod === 'CHIP' ? '▣' : '⌁';
  }
  screen('screenProcessing');
  announce('Esperando respuesta del emisor simulado.');
  const payload = {request_id: requestId, read_method: readMethod, installments: Number($('installments').value)};
  if (pin !== undefined) { payload.pin = pin; payload.attempt_id = crypto.randomUUID(); }
  const result = await api('/api/card/submit', payload);
  if (result.action === 'INSERT_CARD') {
    needsChip = true;
    screen('screenCard');
    $('cardInstruction').textContent = 'Insertá el chip';
    $('cardHint').textContent = 'El emisor ficticio pide cambiar la forma de lectura.';
    $('cardInstructionIcon').textContent = '▣';
    announce('Arrastrá la misma tarjeta a la ranura inferior.');
  } else if (result.action === 'PIN_REQUIRED' || result.action === 'PIN_RETRY') {
    needsChip = false;
    pinValue = '';
    $('pinTitle').textContent = result.action === 'PIN_RETRY' ? 'PIN incorrecto' : 'Ingresá tu PIN';
    $('pinAttempts').textContent = `Intentos restantes: ${result.attempts_remaining}`;
    screen('screenPin');
    $('pinKeypad').querySelector('button').focus();
    announce(`PIN ficticio requerido. Intentos restantes: ${result.attempts_remaining}.`);
  } else await finish(result);
}
async function startCard(method) {
  readMethod = method;
  needsChip = false;
  requestId = crypto.randomUUID();
  clearTicket();
  $('receiptPaper').classList.remove('is-printing', 'is-printed');
  await api('/api/card/start', {request_id: requestId, amount, card_id: selectedCardId});
  $('cardAmount').textContent = money(amount);
  $('pinAmount').textContent = money(amount);
  await processCard();
}
function updateQrCountdown() {
  const seconds = Math.max(0, Math.ceil((qrExpiry - Date.now()) / 1000));
  $('qrTimer').textContent = `Vence en ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}
function pollQr(id) {
  qrTimer = setTimeout(async () => {
    if (id !== activeQr) return;
    if (busy) { pollQr(id); return; }
    try {
      const result = await api(`/api/qr/status?request_id=${encodeURIComponent(id)}`);
      if (result.status === 'PENDING') pollQr(id);
      else {
        stopQrPolling();
        activeQr = null;
        await finish(result);
        updateControls();
      }
    } catch (error) {
      $('qrTimer').textContent = 'Sin conexión; reintentando…';
      announce(`No se pudo consultar el QR: ${error.message}`);
      pollQr(id);
    }
  }, 1400);
}
async function startQr() {
  await run(async () => {
    requestId = crypto.randomUUID();
    clearTicket();
    $('receiptPaper').classList.remove('is-printing', 'is-printed');
    screen('screenProcessing');
    const qr = await api('/api/qr/start', {request_id: requestId, amount});
    activeQr = requestId;
    $('qrAmount').textContent = money(amount);
    $('qrImage').innerHTML = qr.svg;
    $('qrLink').href = qr.url;
    qrExpiry = new Date(qr.expires_at).getTime();
    updateQrCountdown();
    qrTicker = setInterval(updateQrCountdown, 1000);
    screen('screenQr');
    announce('Escaneá el QR y confirmá en el teléfono.');
    pollQr(requestId);
  });
}
async function showTicket(id, returnTo = 'screenHistory', printPaper = false) {
  const ticket = await api(`/api/ticket/${encodeURIComponent(id)}`);
  $('ticket').textContent = [
    ticket.merchant, `Terminal: ${ticket.terminal}`, `Operador: ${ticket.operator}`,
    localTime(ticket.issued_at), '----------------------------',
    `${ticket.kind} · ${ticket.status}`,
    `${ticket.card_type || 'QR'} ${ticket.last4 ? '•••• ' + ticket.last4 : ''}`,
    ticket.read_method || '', ticket.installments ? `${ticket.installments} cuota(s)` : '',
    `Gs. ${money(ticket.amount)}`, `Autorización: ${ticket.authorization_code}`,
    `Operación: ${ticket.ticket_number}`,
    ticket.original_id ? `Anula: ${ticket.original_id}` : '',
    '----------------------------', ticket.legend
  ].filter(Boolean).join('\n');
  lastTicketId = id;
  ticketReturn = returnTo;
  $('printTicket').disabled = false;
  if (printPaper) {
    $('paperAmount').textContent = money(ticket.amount);
    $('receiptPaper').classList.remove('is-printing', 'is-printed');
    void $('receiptPaper').offsetWidth;
    $('receiptPaper').classList.add(matchMedia('(prefers-reduced-motion: reduce)').matches ? 'is-printed' : 'is-printing');
  }
  if (!printPaper) screen('screenTicket');
}
async function finish(operation) {
  activeQr = null;
  const approved = operation.status === 'APPROVED';
  const voided = operation.kind === 'VOID';
  $('resultEmblem').textContent = approved ? '✓' : '×';
  $('resultEmblem').classList.toggle('is-declined', !approved);
  $('resultKicker').textContent = approved ? 'OPERACIÓN APROBADA' : operation.status;
  $('resultTitle').textContent = approved ? voided ? 'Venta anulada' : 'Cobro aprobado' : 'Cobro no completado';
  $('resultDetail').textContent = approved ? voided ? 'Se registró la anulación en el lote.' : 'La autorización ficticia quedó registrada.' : reasons[operation.response_code] || operation.response_code || 'No se registró un cobro.';
  $('resultAmount').textContent = money(operation.amount);
  $('viewTicket').classList.toggle('hidden', !approved);
  screen('screenResult');
  announce(`${$('resultTitle').textContent}. ${$('resultDetail').textContent}`);
  if (approved) {
    try { await showTicket(operation.id, 'screenResult', true); }
    catch (error) {
      $('viewTicket').classList.add('hidden');
      announce(`Cobro aprobado; no se pudo cargar el ticket: ${error.message}`);
    }
  }
  try { await refresh(); }
  catch (error) { announce(`No se pudo actualizar el lote: ${error.message}`); }
}
function renderOperations(items) {
  $('operations').replaceChildren();
  if (!items.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'Todavía no hay operaciones.';
    $('operations').append(empty);
    return;
  }
  for (const op of items) {
    const row = document.createElement('div');
    row.className = 'operation-row';
    const title = document.createElement('strong');
    title.textContent = `${op.kind === 'VOID' ? 'Anulación' : op.kind === 'QR' ? 'QR' : op.card_type === 'CREDIT' ? 'Crédito' : 'Débito'} · Gs. ${money(op.amount)}`;
    const status = document.createElement('span');
    status.className = 'operation-status' + (op.status !== 'APPROVED' ? ' is-declined' : '');
    status.textContent = op.status;
    const time = document.createElement('small');
    time.textContent = `${localTime(op.created_at)} · ${op.id.slice(0, 8)}`;
    const actions = document.createElement('div');
    actions.className = 'operation-actions';
    if (op.status === 'APPROVED') {
      const ticket = document.createElement('button');
      ticket.type = 'button';
      ticket.textContent = 'Ver ticket';
      ticket.addEventListener('click', () => run(() => showTicket(op.id)));
      actions.append(ticket);
      if (op.kind !== 'VOID' && batch && !batch.closed_at && op.batch_id === batch.id) {
        const cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.textContent = 'Anular';
        cancel.addEventListener('click', () => {
          if (confirm('¿Registrar una anulación ficticia de esta venta?')) {
            run(async () => finish(await api('/api/void', {request_id: crypto.randomUUID(), original_id: op.id})));
          }
        });
        actions.append(cancel);
      }
    }
    row.append(title, status, time, actions);
    $('operations').append(row);
  }
}
async function refresh() {
  let summary;
  try { summary = await api('/api/batch'); }
  catch (error) {
    if (error.status !== 404) throw error;
    summary = null;
  }
  batch = summary?.batch || null;
  const open = batch && !batch.closed_at;
  $('batchState').textContent = open ? `Lote #${batch.id} abierto` : 'Lote sin abrir';
  if (currentView === 'screenHome') {
    $('screenMessage').textContent = open ? 'Ingresá el importe y tocá Cobrar.' : 'Abrí un lote en el menú inferior para comenzar.';
  }
  $('batchDetail').textContent = !batch ? 'Sin abrir' : `#${batch.id} · ${open ? 'Abierto' : 'Cerrado'}`;
  $('batchOwnerNote').textContent = open && batch.operator_id !== employee?.id
    ? `Lote abierto por ${batch.operator_name}. Solo ese empleado puede cerrarlo.`
    : 'El resumen reúne las operaciones ficticias de esta terminal.';
  $('salesTotal').textContent = `Gs. ${money(summary?.sales)}`;
  $('netTotal').textContent = `Gs. ${money(summary?.net)}`;
  updateControls();
  renderOperations(await api('/api/operations'));
}
function back() {
  if (busy || activeQr) return;
  if (currentView === 'screenCardSelect') screen('screenMethod');
  else if (currentView === 'screenMethod') screen('screenHome');
  else if (currentView === 'screenTicket') screen(ticketReturn);
  else screen('screenHome');
}
function bind() {
  bindCards();
  $('loginForm').addEventListener('submit', event => {
    event.preventDefault();
    if (busy) return;
    run(async () => {
      try {
        employee = await api('/api/auth/login', {code: $('operatorCode').value.trim(), pin: $('operatorPin').value});
        $('operatorPin').value = '';
        $('loginMessage').textContent = '';
        await loadTerminal();
      } catch (error) {
        $('operatorPin').value = '';
        employee = null;
        $('loginMessage').textContent = error.message;
        screen('screenLogin');
      }
    });
  });
  $('logoutButton').addEventListener('click', () => run(async () => {
    await api('/api/auth/logout', {});
    showLogin('Sesión cerrada.');
  }));
  $('backButton').addEventListener('click', back);
  $('navHome').addEventListener('click', () => { if (!busy && !activeQr) resetHome(); });
  $('navBatch').addEventListener('click', () => { if (!busy && !activeQr) screen('screenBatch'); });
  $('navHistory').addEventListener('click', () => { if (!busy && !activeQr) run(async () => { await refresh(); screen('screenHistory'); }); });
  $('keypad').addEventListener('click', event => {
    const key = event.target.closest('button[data-key]')?.dataset.key;
    if (!key || busy || activeQr) return;
    const digits = key === 'back' ? String(amount).slice(0, -1) || '0' : amount === 0 ? key : String(amount) + key;
    if (Number(digits) <= 100_000_000) setAmount(digits);
  });
  $('screenPay').addEventListener('click', chooseAmount);
  $('methodCard').addEventListener('click', () => screen('screenCardSelect'));
  $('methodQr').addEventListener('click', startQr);
  $('pinKeypad').addEventListener('click', event => {
    const key = event.target.closest('button[data-pin]')?.dataset.pin;
    if (!key || busy || currentView !== 'screenPin') return;
    if (key === 'clear') pinValue = '';
    else if (key === 'back') pinValue = pinValue.slice(0, -1);
    else if (pinValue.length < 4) pinValue += key;
    updatePinDisplay();
  });
  $('submitPin').addEventListener('click', () => run(async () => {
    if (!/^\d{4}$/.test(pinValue)) return;
    const pin = pinValue;
    pinValue = '';
    updatePinDisplay();
    await processCard(pin);
  }));
  document.addEventListener('keydown', event => {
    if (currentView !== 'screenPin' || busy) return;
    if (/^\d$/.test(event.key) && pinValue.length < 4) pinValue += event.key;
    else if (event.key === 'Backspace') pinValue = pinValue.slice(0, -1);
    else if (event.key === 'Enter' && pinValue.length === 4) { $('submitPin').click(); return; }
    else return;
    updatePinDisplay();
    event.preventDefault();
  });
  $('cancelQr').addEventListener('click', () => run(async () => {
    const id = activeQr;
    try {
      const result = await api('/api/qr/cancel', {request_id: id});
      stopQrPolling();
      await finish(result);
    } catch (error) {
      $('qrTimer').textContent = 'No se pudo cancelar; reintentá.';
      announce(`El QR sigue pendiente: ${error.message}`);
    }
  }));
  for (const id of ['cancelCard', 'cancelPin']) {
    $(id).addEventListener('click', () => run(async () => {
      const result = await api('/api/card/cancel', {request_id: requestId});
      pinValue = '';
      updatePinDisplay();
      await finish(result);
    }));
  }
  $('newSale').addEventListener('click', resetHome);
  $('viewTicket').addEventListener('click', () => { if (lastTicketId) screen('screenTicket'); });
  $('openBatch').addEventListener('click', () => run(async () => {
    const opened = await api('/api/batch/open', {});
    await refresh();
    screen('screenHome');
    announce(`Lote #${opened.id} abierto. Ya podés cobrar.`);
  }));
  $('closeBatch').addEventListener('click', () => run(async () => {
    const summary = await api('/api/batch/close', {});
    await refresh();
    screen('screenBatch');
    announce(`Lote cerrado. Neto: Gs. ${money(summary.net)}.`);
  }));
  $('refresh').addEventListener('click', () => run(refresh));
  $('printTicket').addEventListener('click', () => window.print());
}
function clock() {
  $('screenClock').textContent = new Intl.DateTimeFormat('es-PY', {
    timeZone: 'America/Asuncion', hour: '2-digit', minute: '2-digit', hour12: false
  }).format(new Date());
}
async function boot() {
  bind();
  clock();
  setInterval(clock, 30_000);
  setAmount(0);
  if (location.protocol === 'file:') {
    showError('Esta página necesita el servidor local. Ejecutá python -m terminal_pos.api y abrí http://127.0.0.1:8875/.');
    return;
  }
  try {
    employee = await api('/api/auth/me');
    await loadTerminal();
  } catch (error) {
    if (error.status === 401) showLogin();
    else showError(`No se pudo conectar con el servidor local: ${error.message}`);
  }
}
boot();
