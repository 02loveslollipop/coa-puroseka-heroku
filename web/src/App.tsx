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
  return <main className="login-page">
    <section className="login-visual" aria-label="Puroseka">
      {/* Static background mock; the animation will be added in a later iteration. */}
      <svg className="login-visual-graph" viewBox="0 0 900 900" fill="none" aria-hidden="true">
        <g stroke="currentColor" strokeWidth="2">
          <path d="M100 160 360 240 640 130 810 350 620 540 760 760 430 700 170 790 100 530 360 460 360 240M100 160 100 530M360 240 620 540 430 700 360 460 100 530M640 130 620 540M360 460 810 350M170 790 430 700" />
          {[[100,160],[360,240],[640,130],[810,350],[620,540],[760,760],[430,700],[170,790],[100,530],[360,460]].map(([cx,cy]) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="15" />)}
        </g>
      </svg>
      <div className="login-brand"><h1>Puroseka</h1><p>Explore events through conversation.</p></div>
    </section>
    <section className="login-panel" aria-label="Sign in">
    <div className="login-card">
    <SpaceBetween size="l">
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
  </div></section></main>;
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
