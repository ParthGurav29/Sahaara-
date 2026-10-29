import { useState, useCallback } from "react";
import { useAuth } from "./AuthContext";
import { navigateTo } from "./useRoute";
import "./LoginView.css";

export default function LoginView() {
  const { login, authEnabled } = useAuth();
  const [username, setUsername] = useState("caregiver");
  const [password, setPassword] = useState("demo123");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(username, password);
      navigateTo("/choose");
    } catch (err: any) {
      setError(err.message || "Login failed");
    } finally {
      setLoading(false);
    }
  }, [username, password, login]);

  if (!authEnabled) {
    return (
      <div className="login-screen">
        <div className="login-card">
          <div className="wordmark">Sahaara</div>
          <p className="muted">Authentication is disabled</p>
          <button className="btn" onClick={() => navigateTo("/choose")}>
            Continue to Chooser
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="wordmark">Sahaara</div>
        <p className="tag">A gentle companion for dementia care</p>
        <form onSubmit={handleSubmit}>
          <label className="field">
            <span>Username</span>
            <input
              id="email"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
              disabled={loading}
            />
          </label>
          <label className="field">
            <span>Password</span>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
              disabled={loading}
            />
          </label>
          {error && <p className="error-message">{error}</p>}
          <button className="btn" type="submit" disabled={loading}>
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}