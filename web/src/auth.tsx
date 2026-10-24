import { createContext, useContext } from "react";

export type Session = { username: string; role: "admin" | "user"; csrf: string };
export const AuthContext = createContext<{ user: Session; signOut: () => Promise<void> } | null>(null);
export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("Authentication is required");
  return value;
}
