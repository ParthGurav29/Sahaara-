import {
  createContext,
  useCallback,
  useContext,
  useState,
  useEffect,
  type ReactNode,
} from "react";
import { apiFetch, apiFetchRaw } from "./api";

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
  /** True when the auth bootstrap could not reach the server (fail closed). */
  authError: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  retryAuth: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [authEnabled, setAuthEnabled] = useState(false);
  const [authError, setAuthError] = useState(false);

  const checkAuthStatus = useCallback(async () => {
    try {
      const statusRes = await apiFetchRaw("/auth/status");
      if (!statusRes.ok) {
        throw new Error(`GET /auth/status -> ${statusRes.status}`);
      }
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
      //
      // A 401 here is the NORMAL "not signed in" answer, not an error: this
      // runs before every login. Only a real transport/server failure marks
      // the provider as errored, and it never falls back to "auth is off".
      const meRes = await apiFetchRaw("/auth/me");
      if (meRes.status === 401) {
        setUser(null);
        return;
      }
      if (!meRes.ok) {
        throw new Error(`GET /auth/me -> ${meRes.status}`);
      }

      const me = await meRes.json();
      setUser({
        user_id: me.user_id,
        username: me.username,
        role: me.role,
        profile_id: me.profile_id,
      });
    } catch (err) {
      console.error("[AuthContext] auth bootstrap failed:", err);
      // Fail closed: an unreachable /auth/status must never be read as
      // "authentication is switched off" and render the caregiver dashboard.
      setAuthError(true);
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void checkAuthStatus();
  }, [checkAuthStatus]);

  /** Re-run the bootstrap from the retry screen. */
  const retryAuth = useCallback(async () => {
    setIsLoading(true);
    setAuthError(false);
    await checkAuthStatus();
  }, [checkAuthStatus]);


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

    // The HttpOnly cookie is the only authentication mechanism.
    // The token in the response is for non-browser clients only.
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
      setUser(null);
    }
  }

  return (
    <AuthContext.Provider
      value={{ user, isLoading, authEnabled, authError, login, logout, retryAuth }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
