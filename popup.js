import { PROFILE_URL } from './core.mjs';

const elements = {
  card: document.querySelector('#statusCard'),
  title: document.querySelector('#statusTitle'),
  message: document.querySelector('#statusMessage'),
  time: document.querySelector('#statusTime'),
  checkin: document.querySelector('#checkinButton'),
  buttonText: document.querySelector('#buttonText'),
  enabled: document.querySelector('#enabled'),
  hour: document.querySelector('#hour'),
  minute: document.querySelector('#minute'),
  nextRun: document.querySelector('#nextRun'),
  openProfile: document.querySelector('#openProfile'),
};

const STATUS_COPY = {
  success: ['签到成功', 'success'],
  already: ['今日已签到', 'success'],
  login_required: ['需要重新登录', 'warning'],
  disabled: ['签到未开启', 'warning'],
  unknown: ['结果待确认', 'warning'],
  error: ['签到失败', 'error'],
};

initialize();

elements.checkin.addEventListener('click', async () => {
  setBusy(true);
  try {
    const result = await chrome.runtime.sendMessage({ type: 'run-checkin' });
    renderResult(result);
  } catch (error) {
    renderResult({ status: 'error', message: error.message, timestamp: Date.now() });
  } finally {
    setBusy(false);
  }
});

elements.enabled.addEventListener('change', saveSettings);
elements.hour.addEventListener('change', saveSettings);
elements.minute.addEventListener('change', saveSettings);
elements.openProfile.addEventListener('click', () => {
  chrome.tabs.create({ url: PROFILE_URL });
});

async function initialize() {
  const state = await chrome.runtime.sendMessage({ type: 'get-state' });
  renderState(state);
}

async function saveSettings() {
  const state = await chrome.runtime.sendMessage({
    type: 'save-settings',
    settings: {
      enabled: elements.enabled.checked,
      hour: Number(elements.hour.value),
      minute: Number(elements.minute.value),
    },
  });
  renderState(state);
}

function renderState(state) {
  elements.enabled.checked = state.settings.enabled;
  elements.hour.value = String(state.settings.hour).padStart(2, '0');
  elements.minute.value = String(state.settings.minute).padStart(2, '0');
  elements.hour.disabled = !state.settings.enabled;
  elements.minute.disabled = !state.settings.enabled;

  if (state.settings.enabled && state.nextRun) {
    elements.nextRun.textContent = `下次：${formatDateTime(state.nextRun)}`;
  } else {
    elements.nextRun.textContent = state.settings.enabled
      ? '正在安排下一次任务'
      : '已关闭';
  }

  if (state.lastResult) {
    renderResult(state.lastResult);
  } else {
    elements.card.className = 'status-card neutral';
    elements.title.textContent = '尚未执行';
    elements.message.textContent = '点击“立即签到”可验证登录状态和接口兼容性';
    elements.time.textContent = '';
  }
}

function renderResult(result) {
  const [title, tone] = STATUS_COPY[result?.status] || ['未知状态', 'neutral'];
  elements.card.className = `status-card ${tone}`;
  elements.title.textContent = title;
  elements.message.textContent = result?.message || '没有详细信息';
  elements.time.textContent = result?.timestamp
    ? `执行于 ${formatDateTime(result.timestamp)}`
    : '';
}

function setBusy(busy) {
  elements.checkin.disabled = busy;
  elements.buttonText.textContent = busy ? '正在安全签到…' : '立即签到';
}

function formatDateTime(value) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
}
