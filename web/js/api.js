// Signed Mini App initData — proves to the API that the caller is a member of the chat.
const INIT_DATA = (window.Telegram && window.Telegram.WebApp) ? (window.Telegram.WebApp.initData || '') : '';

export function authHeaders() { return { 'Authorization': 'tma ' + INIT_DATA }; }

// <video> tags can't send headers, so the video endpoint takes initData as a query param.
export function videoUrl(fileId) { return '/api/video?file_id=' + encodeURIComponent(fileId) + '&auth=' + encodeURIComponent(INIT_DATA); }
