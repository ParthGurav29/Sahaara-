/**
 * mediaPlayer.ts
 *
 * One shared audio element for task playback in the TEXT chat — a local track
 * from /media or a radio stream. The voice session keeps its own element
 * (useVoiceSession) because the mic gate has to reason about exactly one
 * playback source; this module is only for the typed path.
 *
 * Nothing here invents state: `playMediaUrl` reports failure through the
 * promise it swallows, and `stopMediaUrl` is safe to call when nothing plays.
 */

let current: HTMLAudioElement | null = null;

/** Play `url`, stopping whatever this module was already playing. */
export function playMediaUrl(url: string): void {
  stopMediaUrl();

  const audio = new Audio(url);
  audio.addEventListener("ended", () => {
    if (current === audio) current = null;
  });

  audio.play().catch((error) => {
    // Autoplay policies and dead streams both land here. Log it and leave the
    // UI honest — the backend already told the person what it attempted.
    console.error("Media playback failed:", error);
    if (current === audio) current = null;
  });

  current = audio;
}

/** Stop and release the shared element. Safe when nothing is playing. */
export function stopMediaUrl(): void {
  if (!current) return;
  try {
    current.pause();
  } catch {
    /* ignore */
  }
  current.removeAttribute("src");
  current = null;
}

/** The URL currently playing through this module, or null. */
export function currentMediaUrl(): string | null {
  return current ? current.src : null;
}
