import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { deriveCanWrite } from "../lib/roles";
import type { AuthUser } from "../types";

interface AuthState {
  user: AuthUser | null;
  isLoading: boolean;
  // UX only — mirrors backend/app/auth.py's require_write() gate so the UI
  // doesn't offer controls that will 403. Not itself a security boundary;
  // see I.8 in docs/PLAN-auth-rbac-completion.md. False (not true) while
  // user is null, so it defaults closed rather than open pre-auth/on logout.
  canWrite: boolean;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

export function useAuth(): AuthState {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const me = await api.getMe();
      setUser(me);
    } catch {
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const logout = useCallback(async () => {
    await api.logout();
    setUser(null);
  }, []);

  const canWrite = deriveCanWrite(user?.role);

  return { user, isLoading, canWrite, logout, refresh };
}
