import { useEffect, useState } from "react";
import { Alert, Box, Button, Container, ContentLayout, FormField, Header, Input, KeyValuePairs, SpaceBetween, StatusIndicator, Toggle } from "@cloudscape-design/components";
import { api, TokenUsage } from "./api";
import { useAuth } from "./auth";

export function TokenSettings() {
  const { user } = useAuth();
  const [usage, setUsage] = useState<TokenUsage | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [limit, setLimit] = useState("100000");
  const [windowSeconds, setWindowSeconds] = useState("3600");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const valid = Number.isInteger(Number(limit)) && Number(limit) > 0 && Number(limit) <= 1e9 && Number.isInteger(Number(windowSeconds)) && Number(windowSeconds) >= 60 && Number(windowSeconds) <= 2592000;
  async function load() {
    try {
      const value = await api<TokenUsage>("/usage");
      setUsage(value); setEnabled(value.enabled); setLimit(String(value.token_limit)); setWindowSeconds(String(value.window_seconds));
    } catch (e) { setError((e as Error).message); }
  }
  useEffect(() => { void load(); }, []);
  async function save() {
    if (!valid) return;
    setBusy(true); setError(""); setSaved(false);
    try {
      setUsage(await api<TokenUsage>("/admin/token-policy", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled, token_limit: Number(limit), window_seconds: Number(windowSeconds) }) }));
      setSaved(true);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <ContentLayout header={<Header variant="h1" description="Control the shared account’s token allowance from the COA.">Token limits</Header>}>
    <SpaceBetween size="l">
      {error && <Alert type="error">{error}</Alert>}
      {saved && <Alert type="success" dismissible onDismiss={() => setSaved(false)}>Token policy saved. Existing usage has been preserved.</Alert>}
      <Container header={<Header variant="h2" actions={<Button iconName="refresh" onClick={() => void load()}>Refresh usage</Button>}>Current usage</Header>}>
        <KeyValuePairs columns={3} items={[
          { label: "Account", value: user.username },
          { label: "Enforcement", value: <StatusIndicator type={usage?.enabled ? "success" : "stopped"}>{usage?.enabled ? "On" : "Off"}</StatusIndicator> },
          { label: "Used tokens", value: usage?.used_tokens.toLocaleString() ?? "Loading" },
          { label: "Reserved for active calls", value: usage?.reserved_tokens.toLocaleString() ?? "Loading" },
          { label: "Current window resets", value: usage ? new Date(usage.resets_at).toLocaleString() : "Loading" },
          { label: "Configured allowance", value: usage ? `${usage.token_limit.toLocaleString()} / ${usage.window_seconds} seconds` : "Loading" },
        ]} />
      </Container>
      <Container header={<Header variant="h2">Token policy</Header>} footer={<Box float="right"><Button variant="primary" loading={busy} disabled={!valid || !usage} onClick={() => void save()}>Save policy</Button></Box>}>
        <SpaceBetween size="l">
          <Toggle checked={enabled} onChange={({ detail }) => setEnabled(detail.checked)} disabled={busy}>Enforce token limit</Toggle>
          <FormField label="Tokens per window" description="Input and output tokens across query generation and response synthesis." errorText={Number(limit) <= 0 ? "Enter a positive whole number." : undefined}>
            <Input type="number" value={limit} onChange={({ detail }) => setLimit(detail.value)} disabled={busy} />
          </FormField>
          <FormField label="Window duration in seconds" description="For example, 3600 is one hour. A duration change takes effect after the current window resets.">
            <Input type="number" value={windowSeconds} onChange={({ detail }) => setWindowSeconds(detail.value)} disabled={busy} />
          </FormField>
          <Box color="text-body-secondary">While enforcement is off, usage is still metered. When enabled, each model call reserves a conservative input/output allowance before it runs; the reported Mistral usage replaces that reservation afterward. Timeouts and interrupted calls retain their reservation as a conservative charge. Restarting the app or changing this policy does not clear active usage.</Box>
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  </ContentLayout>;
}
