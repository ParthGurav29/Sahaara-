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
  isLoading: boolean;
  authEnabled: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
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
        return;
      }

      // Restore the cookie session. The token itself never reaches JavaScript:
      // it stays in the HttpOnly cookie, and HTTP, SSE and the voice WebSocket
      // all authenticate from that cookie.
      const meRes = await apiFetch("/auth/me");
      if (!meRes.ok) {
        console.warn("[AuthContext] /auth/me failed:", meRes.status);
        setUser(null);
        return;
      }

      const me = await meRes.json();
      setUser({
        user_id: me.user_id,
        username: me.username,
        role: me.role,
        profile_id: me.profile_id,
      });
    } catch (err) {
      console.error("[AuthContext] checkAuthStatus failed:", err);
      setAuthEnabled(false);
      setUser(null);
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

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || "Login failed");
    }

    // Store token in localStorage as fallback for WebSocket connections
    // (HttpOnly cookie is primary, but WebSocket proxy may not forward cookies)
    if (data.token) {
      localStorage.setItem("sahaara_token", data.token);
    }

    // The response still carries a token for non-browser clients; the browser
    // deliberately ignores it and relies on the HttpOnly cookie.
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
      localStorage.removeItem("sahaara_token");
      setUser(null);
    }
  }

  return (
    <AuthContext.Provider value={{ user, isLoading, authEnabled, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
