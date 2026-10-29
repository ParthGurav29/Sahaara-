import { useEffect, useState } from "react";
import { useVoiceSession } from "./useVoiceSession";
import { useCompanionChannel } from "./useCompanionChannel";
import SahaaraAvatar from "./SahaaraAvatar";
import CompanionAlarm from "./CompanionAlarm";
import "./CompanionView.css";

function useNow(intervalMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

interface ReminderState {
  label: string;
  endTime: number;
  active: boolean;
}

export default function CompanionView() {
  const now = useNow();
  const channel = useCompanionChannel();
  const { state, errorKind } = useVoiceSession();

  const time = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const weekday = now.toLocaleDateString([], { weekday: "long" });
  const date = now.toLocaleDateString([], { day: "numeric", month: "long", year: "numeric" });

  const [captionsEnabled, setCaptionsEnabled] = useState(false);
  const [showPin, setShowPin] = useState(false);
  const [pinError, setPinError] = useState("");
  const [reminder, setReminder] = useState<ReminderState>({ label: "", endTime: 0, active: false });
  const [nowPlaying, setNowPlaying] = useState<{ title: string; playing: boolean } | null>(null);

  // Update reminder countdown
  useEffect(() => {
    if (!reminder.active) return;
    const interval = setInterval(() => {
      const remaining = Math.max(0, Math.round((reminder.endTime - Date.now()) / 1000));
      if (remaining === 0) {
        setReminder({ label: "", endTime: 0, active: false });
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [reminder.active, reminder.endTime]);

  // Handle companion channel events
  useEffect(() => {
    if (channel.nowPlaying) {
      setNowPlaying({ title: channel.nowPlaying.title, playing: channel.nowPlaying.kind === "song" });
    } else if (channel.nowPlaying === null && nowPlaying) {
      setNowPlaying(null);
    }
    if (channel.reminder) {
      setReminder({
        label: channel.reminder.label,
        endTime: channel.reminder.fireAt * 1000,
        active: true,
      });
    } else if (channel.reminder === null) {
      setReminder({ label: "", endTime: 0, active: false });
    }
  }, [channel.nowPlaying, channel.reminder, nowPlaying]);

  const handleLock = () => {
    setShowPin(true);
    setPinError("");
  };

  const handlePinSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const form = e.currentTarget as HTMLFormElement;
    const input = form.querySelector("input") as HTMLInputElement;
    if (input.value === "1234") {
      setShowPin(false);
      window.location.href = "/caregiver";
    } else {
      setPinError("That PIN isn't right.");
    }
  };

  const handlePinCancel = () => {
    setShowPin(false);
    setPinError("");
  };

  const stateLabel = {
    idle: "Tap to talk",
    listening: "Listening…",
    thinking: "One moment…",
    speaking: "Sahaara",
    error: errorKind === "microphone" ? "Microphone unavailable" : "Connection lost",
  }[state] || "Tap to talk";

  const remainingSeconds = reminder.active ? Math.max(0, Math.round((reminder.endTime - Date.now()) / 1000)) : 0;
  const remMin = Math.floor(remainingSeconds / 60);
  const remSec = remainingSeconds % 60;
  const remTime = `${remMin}:${String(remSec).padStart(2, "0")}`;

  const nextRoutine = channel.routine
    ? `${channel.routine.label} · ${channel.routine.time}`
    : "Lunch · 1:00 PM";

  return (
    <div className="companion-screen">
      {/* Top bar */}
      <div className="cbar">
        <button
          className="chip"
          id="capbtn"
          aria-pressed={captionsEnabled}
          onClick={() => setCaptionsEnabled((v) => !v)}
        >
          Captions
        </button>
        <button className="chip" id="lockbtn" onClick={handleLock}>
          Caregiver
        </button>
      </div>

      {/* Main layout */}
      <div className="cwrap">
        {/* Stage - left side */}
        <div className="stage" id="stage">
          <SahaaraAvatar size={380} />
          <div className="state" id="state">{stateLabel}</div>
          {captionsEnabled && channel.caption && (
            <p className="caption show" id="caption" aria-live="polite">
              {channel.caption.text}
            </p>
          )}
          {!captionsEnabled && channel.caption && (
            <p className="caption" id="caption" aria-live="polite">
              {channel.caption.text}
            </p>
          )}
        </div>

        {/* Strip - right side */}
        <aside className="strip" aria-label="Today">
          <div className="tile">
            <small id="wd">{weekday}</small>
            <div className="clock" id="clk">{time}</div>
            <div className="date" id="dt">{date}</div>
          </div>
          <div className="tile" id="tNow" hidden={!nowPlaying}>
            <small>Now playing</small>
            <div className="big">
              <span className="eq">
                <i></i><i></i><i></i>
              </span>
              <span id="song">{nowPlaying?.title}</span>
            </div>
          </div>
          <div className="tile" id="tRem" hidden={!reminder.active}>
            <small id="remLbl">{reminder.label || "Reminder"}</small>
            <div className="big" id="remT">{remTime}</div>
          </div>
          <div className="tile">
            <small>Next</small>
            <div className="big">{nextRoutine}</div>
          </div>
        </aside>
      </div>

      {/* Tweaks panel */}
      <div className="twk" id="twk">
        <b>Tweaks (demo)</b>
        <button className="btn sm ghost" onClick={() => setNowPlaying({ title: "Lag Ja Gale", playing: true })}>
          Play / stop a song
        </button>
        <button className="btn sm ghost" onClick={() => setReminder({ label: "Reminder · Tea", endTime: Date.now() + 120000, active: true })}>
          Set 2 min reminder
        </button>
        <button className="btn sm ghost" onClick={() => {
          const r = document.documentElement;
          r.dataset.theme = r.dataset.theme === "dark" ? "light" : "dark";
        }}>
          Toggle dark
        </button>
      </div>
      <button className="twk-toggle" onClick={() => document.getElementById("twk")?.classList.toggle("on")}>
        Tweaks
      </button>

      {/* PIN Modal */}
      <div className="modal" id="pin" style={{ display: showPin ? "grid" : "none" }}>
        <div className="card">
          <h3>Caregiver PIN</h3>
          <p className="muted" style={{ fontSize: "0.9rem" }}>
            Demo PIN is 1234
          </p>
          <form onSubmit={handlePinSubmit}>
            <input
              id="pinin"
              inputMode="numeric"
              maxLength={4}
              aria-label="PIN"
              autoFocus
              placeholder="1234"
            />
            <div className="err" id="pinerr">{pinError}</div>
            <div style={{ display: "flex", gap: "0.5rem", justifyContent: "center", marginTop: "0.5rem" }}>
              <button type="button" className="btn ghost" onClick={handlePinCancel}>
                Cancel
              </button>
              <button type="submit" className="btn">Unlock</button>
            </div>
          </form>
        </div>
      </div>
      <CompanionAlarm />
    </div>
  );
}