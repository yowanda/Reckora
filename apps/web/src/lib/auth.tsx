import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { api, readToken, unwrap, writeToken } from "@/api/client";
import type { UserPublic } from "@/api/types";

type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; token: string; user: UserPublic };

interface AuthContextValue {
  state: AuthState;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

async function fetchMe(): Promise<UserPublic> {
  return unwrap(await api.GET("/api/v1/auth/me"));
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(() => {
    const token = readToken();
    return token === null ? { status: "anonymous" } : { status: "loading" };
  });

  const refresh = useCallback(async () => {
    const token = readToken();
    if (token === null) {
      setState({ status: "anonymous" });
      return;
    }
    try {
      const user = await fetchMe();
      setState({ status: "authenticated", token, user });
    } catch {
      writeToken(null);
      setState({ status: "anonymous" });
    }
  }, []);

  useEffect(() => {
    if (state.status === "loading") {
      void refresh();
    }
  }, [state.status, refresh]);

  const login = useCallback(
    async (username: string, password: string) => {
      const body = new URLSearchParams({ username, password });
      const result = await api.POST("/api/v1/auth/token", {
        body: body.toString() as never,
        bodySerializer: (input: unknown) => input as string,
        headers: { "content-type": "application/x-www-form-urlencoded" },
      });
      const tokenResponse = unwrap(result);
      writeToken(tokenResponse.access_token);
      const user = await fetchMe();
      setState({
        status: "authenticated",
        token: tokenResponse.access_token,
        user,
      });
    },
    [],
  );

  const logout = useCallback(() => {
    writeToken(null);
    setState({ status: "anonymous" });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ state, login, logout, refresh }),
    [state, login, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return ctx;
}
