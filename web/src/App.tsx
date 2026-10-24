import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Alert, Box, Button, Container, Form, FormField, Header, Input, SpaceBetween, Spinner, TextContent } from "@cloudscape-design/components";
import { api, setCsrf } from "./api";
import { AuthContext, Session } from "./auth";
import { UserChat } from "./chat";
import { DataAttribution } from "./data-attribution";
const AdminApp = lazy(() => import("./AdminApp"));

function Login({ onLogin }: { onLogin: (user: Session) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit() {
    if (busy || !username.trim() || !password) return;
    setBusy(true); setError("");
    try {
      const session = await api<Session>("/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) });
      setPassword(""); onLogin(session);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <main className="login-page"><div className="login-card">
    <SpaceBetween size="l">
      <Box textAlign="center"><Box variant="h1">Puroseka</Box><Box color="text-body-secondary">Explore events through conversation</Box></Box>
      <Container header={<Header variant="h2">Sign in</Header>}>
        <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
          <Form actions={<Button variant="primary" formAction="submit" loading={busy} disabled={!username.trim() || !password}>Sign in</Button>}>
            <SpaceBetween size="l">
              <FormField label="Email"><Input value={username} onChange={({ detail }) => setUsername(detail.value)} autoComplete="username" type="email" disabled={busy} /></FormField>
              <FormField label="Password"><Input value={password} onChange={({ detail }) => setPassword(detail.value)} autoComplete="current-password" type="password" disabled={busy} /></FormField>
              {error && <Alert type="error" header="Unable to sign in">{error}</Alert>}
            </SpaceBetween>
          </Form>
        </form>
      </Container>
      <TextContent><Box textAlign="center" color="text-body-secondary">Use the account provided for this demonstration.</Box></TextContent>
      <DataAttribution />
    </SpaceBetween>
  </div></main>;
}

export default function App() {
  const [user, setUser] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  function accept(session: Session) { setCsrf(session.csrf); setUser(session); }
  useEffect(() => {
    api<Session>("/auth/me").then(accept).catch(() => setUser(null)).finally(() => setLoading(false));
    const expired = () => { setCsrf(""); setUser(null); };
    window.addEventListener("coa-session-expired", expired);
    return () => window.removeEventListener("coa-session-expired", expired);
  }, []);
  async function signOut() {
    await api("/auth/logout", { method: "POST" });
    setCsrf(""); setUser(null);
  }
  if (loading) return <Box padding="xxl" textAlign="center"><Spinner size="large" /> Checking your session</Box>;
  if (!user) return <Login onLogin={accept} />;
  return <AuthContext.Provider value={{ user, signOut }}><Routes>
    <Route path="/admin/*" element={user.role === "admin" ? <Suspense fallback={<Box padding="l"><Spinner /> Loading COA administration</Box>}><AdminApp /></Suspense> : <Navigate to="/" replace />} />
    <Route path="/ontology" element={<Navigate to="/admin/ontology" replace />} />
    <Route path="/playground" element={<Navigate to="/admin/playground" replace />} />
    <Route path="*" element={<UserChat />} />
  </Routes></AuthContext.Provider>;
}
