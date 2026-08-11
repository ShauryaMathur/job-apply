/**
 * "Pull my focus back" notifier for long-running background tasks (e.g. Scout
 * scraping + resume generation). No external deps, no service worker — just
 * the Notification API plus a synthesized beep, both gated on the tab being
 * hidden/unfocused so they don't interrupt you if you're already watching.
 */

let audioCtx: AudioContext | null = null;

function getAudioContext(): AudioContext | null {
  if (typeof window === "undefined") return null;
  const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  if (!audioCtx) audioCtx = new Ctor();
  return audioCtx;
}

/** Plays a short two-tone chime. Safe to call repeatedly. */
export function playChime() {
  const ctx = getAudioContext();
  if (!ctx) return;
  if (ctx.state === "suspended") ctx.resume();

  const now = ctx.currentTime;
  const tones = [
    { freq: 880, start: 0, dur: 0.12 },
    { freq: 1175, start: 0.13, dur: 0.18 },
  ];

  for (const { freq, start, dur } of tones) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0, now + start);
    gain.gain.linearRampToValueAtTime(0.15, now + start + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + start + dur);
    osc.connect(gain).connect(ctx.destination);
    osc.start(now + start);
    osc.stop(now + start + dur + 0.02);
  }
}

/**
 * Ask for desktop notification permission. Call this from inside a user
 * gesture handler (e.g. a button's onClick) — browsers may ignore the prompt
 * otherwise. Only prompts once; a prior grant/denial is reused.
 */
export function requestNotifyPermission() {
  if (typeof window === "undefined" || !("Notification" in window)) return;
  if (Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
}

/**
 * Fires a desktop notification + chime if the tab is currently hidden or
 * unfocused. Clicking the notification refocuses this tab/window. No-op if
 * you're already looking at the page.
 */
export function notifyTaskDone(title: string, body?: string) {
  if (typeof document === "undefined") return;
  if (document.visibilityState === "visible" && document.hasFocus()) return;

  playChime();

  if ("Notification" in window && Notification.permission === "granted") {
    const n = new Notification(title, { body, tag: "job-apply-task" });
    n.onclick = () => {
      window.focus();
      n.close();
    };
  }
}
