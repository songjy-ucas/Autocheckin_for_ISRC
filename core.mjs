export const SITE_ORIGIN = 'https://llmapi.isrc.ac.cn';
export const PROFILE_URL = `${SITE_ORIGIN}/profile`;
export const DAILY_ALARM = 'daily-checkin';
export const RETRY_ALARM = 'daily-checkin-retry';

export const DEFAULT_SETTINGS = Object.freeze({
  enabled: true,
  hour: 8,
  minute: 10,
});

export function localDateKey(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function localMonthKey(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  return `${year}-${month}`;
}

export function nextDailyRun(hour, minute, now = new Date()) {
  const next = new Date(now);
  next.setHours(Number(hour), Number(minute), 0, 0);
  if (next.getTime() <= now.getTime()) {
    next.setDate(next.getDate() + 1);
  }
  return next;
}

export function clampSchedule(settings = {}) {
  const hour = Math.min(23, Math.max(0, Number(settings.hour) || 0));
  const minute = Math.min(59, Math.max(0, Number(settings.minute) || 0));
  return {
    enabled: settings.enabled !== false,
    hour,
    minute,
  };
}

export function isSuccessfulStatus(status) {
  return status === 'success' || status === 'already';
}

export function safeMessage(value, fallback = '未知响应') {
  if (typeof value !== 'string') return fallback;
  const compact = value.replace(/\s+/g, ' ').trim();
  return compact ? compact.slice(0, 240) : fallback;
}
