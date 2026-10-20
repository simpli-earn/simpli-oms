import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

const STORAGE_KEY = "oms_api_key";

type AuthValue = {
  apiKey: string | null;
  saveKey: (value: string) => void;
  clearKey: () => void;
};

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [apiKey, setApiKey] = useState<string | null>(() => sessionStorage.getItem(STORAGE_KEY));

  useEffect(() => {
    const onUnauthorized = () => {
      sessionStorage.removeItem(STORAGE_KEY);
      setApiKey(null);
    };
    window.addEventListener("oms-unauthorized", onUnauthorized);
    return () => window.removeEventListener("oms-unauthorized", onUnauthorized);
  }, []);

  const value: AuthValue = {
    apiKey,
    saveKey: (next) => {
      sessionStorage.setItem(STORAGE_KEY, next);
      setApiKey(next);
    },
    clearKey: () => {
      sessionStorage.removeItem(STORAGE_KEY);
      setApiKey(null);
    },
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("AuthProvider is missing");
  }
  return value;
}
