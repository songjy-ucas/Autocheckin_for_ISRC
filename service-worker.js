import {
  DAILY_ALARM,
  DEFAULT_SETTINGS,
  PROFILE_URL,
  RETRY_ALARM,
  SITE_ORIGIN,
  clampSchedule,
  isSuccessfulStatus,
  localDateKey,
  nextDailyRun,
  safeMessage,
} from './core.mjs';

let activeRun = null;

chrome.runtime.onInstalled.addListener(async () => {
  const { settings } = await chrome.storage.local.get('settings');
  await chrome.storage.local.set({
    settings: clampSchedule({ ...DEFAULT_SETTINGS, ...settings }),
  });
  await scheduleDailyAlarm();
  await updateBadgeFromStorage();
});

chrome.runtime.onStartup.addListener(async () => {
  await scheduleDailyAlarm();
  await updateBadgeFromStorage();
  await maybeCatchUpAfterStartup();
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== DAILY_ALARM && alarm.name !== RETRY_ALARM) return;
  const { settings } = await chrome.storage.local.get('settings');
  if (settings?.enabled === false) return;
  await runCheckin(alarm.name === DAILY_ALARM ? 'schedule' : 'retry');
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === 'run-checkin') {
    runCheckin('manual').then(sendResponse);
    return true;
  }

  if (message?.type === 'save-settings') {
    saveSettings(message.settings).then(sendResponse);
    return true;
  }

  if (message?.type === 'get-state') {
    getPublicState().then(sendResponse);
    return true;
  }

  return false;
});

async function saveSettings(incoming) {
  const settings = clampSchedule(incoming);
  await chrome.storage.local.set({ settings });
  await scheduleDailyAlarm();
  if (!settings.enabled) {
    await chrome.alarms.clear(RETRY_ALARM);
    await setBadge('OFF', '#64748b', '自动签到已关闭');
  } else {
    await updateBadgeFromStorage();
  }
  return getPublicState();
}

async function scheduleDailyAlarm() {
  const stored = await chrome.storage.local.get('settings');
  const settings = clampSchedule({ ...DEFAULT_SETTINGS, ...stored.settings });
  await chrome.alarms.clear(DAILY_ALARM);
  if (!settings.enabled) return;
  const next = nextDailyRun(settings.hour, settings.minute);
  await chrome.alarms.create(DAILY_ALARM, {
    when: next.getTime(),
    periodInMinutes: 24 * 60,
  });
}

async function maybeCatchUpAfterStartup() {
  const { settings, lastResult } = await chrome.storage.local.get([
    'settings',
    'lastResult',
  ]);
  if (settings?.enabled === false) return;
  if (
    lastResult?.date === localDateKey() &&
    isSuccessfulStatus(lastResult.status)
  ) {
    return;
  }

  const now = new Date();
  const scheduledToday = new Date(now);
  scheduledToday.setHours(
    settings?.hour ?? DEFAULT_SETTINGS.hour,
    settings?.minute ?? DEFAULT_SETTINGS.minute,
    0,
    0,
  );
  if (now >= scheduledToday) {
    await chrome.alarms.create(RETRY_ALARM, { when: Date.now() + 20_000 });
  }
}

async function getPublicState() {
  const [{ settings, lastResult }, alarm] = await Promise.all([
    chrome.storage.local.get(['settings', 'lastResult']),
    chrome.alarms.get(DAILY_ALARM),
  ]);
  return {
    settings: clampSchedule({ ...DEFAULT_SETTINGS, ...settings }),
    lastResult: lastResult || null,
    nextRun: alarm?.scheduledTime || null,
  };
}

async function runCheckin(trigger) {
  if (activeRun) return activeRun;
  activeRun = performCheckin(trigger).finally(() => {
    activeRun = null;
  });
  return activeRun;
}

async function performCheckin(trigger) {
  const startedAt = Date.now();
  let tab = null;
  let createdTab = false;

  try {
    const tabs = await chrome.tabs.query({ url: `${SITE_ORIGIN}/*` });
    tab = tabs.find((item) => item.status === 'complete') || tabs[0] || null;

    if (!tab) {
      tab = await chrome.tabs.create({ url: PROFILE_URL, active: false });
      createdTab = true;
    }

    await waitForTabComplete(tab.id, 30_000);
    const execution = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: 'MAIN',
      func: checkinInsideSite,
      args: [SITE_ORIGIN],
    });
    const raw = execution?.[0]?.result;
    const result = normalizeResult(raw, trigger, startedAt);
    await saveResultAndBadge(result);

    if (createdTab && result.status !== 'login_required') {
      await chrome.tabs.remove(tab.id).catch(() => {});
    } else if (createdTab && result.status === 'login_required' && trigger === 'manual') {
      await chrome.tabs.update(tab.id, { active: true });
    } else if (createdTab) {
      await chrome.tabs.remove(tab.id).catch(() => {});
    }

    await planRetry(result, trigger);
    return result;
  } catch (error) {
    if (createdTab && tab?.id) {
      await chrome.tabs.remove(tab.id).catch(() => {});
    }
    const result = normalizeResult(
      {
        status: 'error',
        message: error?.message || '扩展执行失败',
      },
      trigger,
      startedAt,
    );
    await saveResultAndBadge(result);
    await planRetry(result, trigger);
    return result;
  }
}

function waitForTabComplete(tabId, timeoutMs) {
  return new Promise(async (resolve, reject) => {
    const current = await chrome.tabs.get(tabId).catch(() => null);
    if (current?.status === 'complete') {
      resolve();
      return;
    }

    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error('等待网站加载超时'));
    }, timeoutMs);

    function listener(updatedId, changeInfo) {
      if (updatedId === tabId && changeInfo.status === 'complete') {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }

    chrome.tabs.onUpdated.addListener(listener);
  });
}

function normalizeResult(raw, trigger, startedAt) {
  const allowed = new Set([
    'success',
    'already',
    'disabled',
    'login_required',
    'error',
    'unknown',
  ]);
  const status = allowed.has(raw?.status) ? raw.status : 'error';
  return {
    status,
    message: safeMessage(raw?.message, '签到返回了无法识别的结果'),
    reward: Number.isFinite(Number(raw?.reward)) ? Number(raw.reward) : null,
    protocol: typeof raw?.protocol === 'string' ? raw.protocol : null,
    trigger,
    date: localDateKey(),
    timestamp: Date.now(),
    durationMs: Date.now() - startedAt,
  };
}

async function saveResultAndBadge(result) {
  await chrome.storage.local.set({ lastResult: result });
  if (isSuccessfulStatus(result.status)) {
    await setBadge('OK', '#16a34a', result.message);
  } else if (result.status === 'login_required') {
    await setBadge('登录', '#d97706', result.message);
  } else if (result.status === 'disabled') {
    await setBadge('OFF', '#64748b', result.message);
  } else {
    await setBadge('!', '#dc2626', result.message);
  }
}

async function setBadge(text, color, title) {
  await chrome.action.setBadgeText({ text });
  await chrome.action.setBadgeBackgroundColor({ color });
  await chrome.action.setTitle({ title: `ISRC 每日签到：${title}` });
}

async function updateBadgeFromStorage() {
  const { settings, lastResult } = await chrome.storage.local.get([
    'settings',
    'lastResult',
  ]);
  if (settings?.enabled === false) {
    await setBadge('OFF', '#64748b', '自动签到已关闭');
  } else if (lastResult) {
    await saveResultAndBadge(lastResult);
  } else {
    await chrome.action.setBadgeText({ text: '' });
  }
}

async function planRetry(result, trigger) {
  await chrome.alarms.clear(RETRY_ALARM);
  if (isSuccessfulStatus(result.status) || result.status === 'disabled') return;
  if (trigger === 'manual') return;

  const { retryState } = await chrome.storage.local.get('retryState');
  const today = localDateKey();
  const count = retryState?.date === today ? Number(retryState.count || 0) : 0;
  if (count >= 3) return;

  const delayMinutes = result.status === 'login_required' ? 360 : 60;
  await chrome.storage.local.set({
    retryState: { date: today, count: count + 1 },
  });
  await chrome.alarms.create(RETRY_ALARM, {
    when: Date.now() + delayMinutes * 60_000,
  });
}

// This function is serialized by chrome.scripting and runs in the target
// site's MAIN world. Keep it self-contained and never return credentials.
async function checkinInsideSite(origin) {
  const month = (() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  })();

  const parseJson = (text) => {
    try {
      return JSON.parse(text);
    } catch {
      return null;
    }
  };

  const findUser = () => {
    for (const key of ['user', 'new-api-user', 'new_api_user']) {
      const value = localStorage.getItem(key);
      if (!value) continue;
      const parsed = parseJson(value);
      if (parsed && typeof parsed === 'object') return parsed;
    }
    return null;
  };

  const localUser = findUser();
  const userId = localUser?.id ?? localUser?.user?.id ?? null;
  let bearer = null;

  const buildHeaders = (json = false) => {
    const headers = { Accept: 'application/json, text/plain, */*' };
    if (json) headers['Content-Type'] = 'application/json';
    if (userId != null) headers['New-Api-User'] = String(userId);
    if (bearer) headers.Authorization = `Bearer ${bearer}`;
    return headers;
  };

  const request = async (path, options = {}) => {
    try {
      const response = await fetch(`${origin}${path}`, {
        method: options.method || 'GET',
        headers: buildHeaders(options.json === true),
        credentials: 'include',
        cache: 'no-store',
        body: options.body,
      });
      const text = await response.text();
      return {
        ok: response.ok,
        status: response.status,
        data: parseJson(text),
        text: text.slice(0, 300),
      };
    } catch (error) {
      return {
        ok: false,
        status: 0,
        data: null,
        text: error?.message || '网络请求失败',
      };
    }
  };

  const responseMessage = (response, fallback) => {
    const value =
      response?.data?.message ||
      response?.data?.msg ||
      response?.data?.error?.message ||
      response?.text;
    return typeof value === 'string' && value.trim()
      ? value.replace(/\s+/g, ' ').trim().slice(0, 240)
      : fallback;
  };

  const isAuthFailure = (response) => {
    if ([401].includes(response?.status)) return true;
    const message = responseMessage(response, '').toLowerCase();
    return /未登录|未授权|登录.*(失效|过期)|unauthori|login required|token.*expired/.test(
      message,
    );
  };

  const refreshAccessToken = async () => {
    const refreshed = await request('/api/user/auth/refresh', {
      method: 'POST',
    });
    const token =
      refreshed?.data?.data?.access_token || refreshed?.data?.access_token || null;
    if (refreshed.ok && token) {
      bearer = token;
      return true;
    }
    return false;
  };

  let statusResponse = await request(`/api/user/checkin?month=${month}`);
  if (isAuthFailure(statusResponse)) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      statusResponse = await request(`/api/user/checkin?month=${month}`);
    }
  }

  if (isAuthFailure(statusResponse)) {
    return {
      status: 'login_required',
      message: '登录状态已失效，请先打开网站并重新登录',
      protocol: 'new-api',
    };
  }

  const statusData = statusResponse?.data?.data;
  if (statusResponse.ok && statusResponse?.data?.success !== false && statusData) {
    if (statusData.enabled === false) {
      return {
        status: 'disabled',
        message: '网站当前未开启每日签到功能',
        protocol: 'new-api-checkin',
      };
    }
    const checked = Boolean(
      statusData?.stats?.checked_in_today ??
        statusData?.checked_in_today ??
        statusData?.checked_in,
    );
    if (checked) {
      return {
        status: 'already',
        message: '今日已经签到，无需重复领取',
        protocol: 'new-api-checkin',
      };
    }
  }

  let protocol = 'new-api-checkin';
  let checkinResponse;
  if ([404, 405].includes(statusResponse.status)) {
    protocol = 'legacy-sign-in';
    checkinResponse = await request('/api/user/sign_in', {
      method: 'POST',
      json: true,
    });
  } else if (!statusResponse.ok || statusResponse?.data?.success === false) {
    return {
      status: statusResponse.status === 0 ? 'unknown' : 'error',
      message: `查询签到状态失败：${responseMessage(statusResponse, `HTTP ${statusResponse.status}`)}`,
      protocol,
    };
  } else {
    checkinResponse = await request('/api/user/checkin', {
      method: 'POST',
      json: true,
    });
  }

  if (isAuthFailure(checkinResponse)) {
    return {
      status: 'login_required',
      message: '登录状态已失效，请先打开网站并重新登录',
      protocol,
    };
  }

  const message = responseMessage(checkinResponse, '签到请求已完成');
  if (/今日已签到|已经签到|已签到|already.*check/i.test(message)) {
    return { status: 'already', message, protocol };
  }

  if (checkinResponse.ok && checkinResponse?.data?.success !== false) {
    const reward =
      checkinResponse?.data?.data?.quota_awarded ??
      checkinResponse?.data?.data?.quota ??
      checkinResponse?.data?.data?.amount ??
      null;
    return { status: 'success', message, reward, protocol };
  }

  if (checkinResponse.status === 0) {
    return {
      status: 'unknown',
      message: `签到请求结果未知：${message}。为避免重复提交，本次不会立即重试`,
      protocol,
    };
  }

  return {
    status: 'error',
    message: `签到失败：${message}`,
    protocol,
  };
}
