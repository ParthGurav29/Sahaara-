import { createContext, useContext, useState, useEffect, type ReactNode } from "react";
import { apiFetch } from "./api";

interface User {
  user_id: number;
  username: string;
  role: "caregiver" | "person";
  profile_id: string;
}

interface AuthContextType {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  authEnabled: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [authEnabled, setAuthEnabled] = useState(false);

  useEffect(() => {
    checkAuthStatus();
  }, []);

  async function checkAuthStatus() {
    try {
      const statusRes = await apiFetch("/auth/status");
      const statusData = await statusRes.json();
      const enabled = Boolean(statusData.enabled);
      setAuthEnabled(enabled);

      if (!enabled) {
        setUser(null);
        setToken(null);
        return;
      }

      // Restore the cookie session. /auth/me returns the current opaque
      // token as well, so SSE and the voice WebSocket can reconnect after
      // a page reload without storing the token in browser storage.
      const meRes = await apiFetch("/auth/me");
      if (!meRes.ok) {
        setUser(null);
        setToken(null);
        return;
      }

      const me = await meRes.json();
      setUser({
        user_id: me.user_id,
        username: me.username,
        role: me.role,
        profile_id: me.profile_id,
      });
      setToken(me.token || null);
    } catch {
      setAuthEnabled(false);
      setUser(null);
      setToken(null);
    } finally {
      setIsLoading(false);
    }
  }

  async function login(username: string, password: string) {
    const res = await apiFetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });

    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || "Login failed");
    }

    const data = await res.json();
    setToken(data.token);
    setUser({
      user_id: data.user_id,
      username: data.username,
      role: data.role,
      profile_id: data.profile_id,
    });
    setIsLoading(false);
  }

  async function logout() {
    try {
      await apiFetch("/auth/logout", {
        method: "POST",
      });
    } catch {
      // Ignore logout errors
    } finally {
      setToken(null);
      setUser(null);
    }
  }

  return (
    <AuthContext.Provider value={{ user, token, isLoading, authEnabled, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
