import { lazy, ReactNode, Suspense, useEffect, useRef, useState } from "react";
import { AppLayout, Alert, Box, Button, ColumnLayout, CopyToClipboard, FormField, Header, KeyValuePairs, LiveRegion, Modal, PromptInput, Select, SideNavigation, SpaceBetween, StatusIndicator, TextContent, Toggle, TopNavigation } from "@cloudscape-design/components";
import { Avatar, ChatBubble, SupportPromptGroup } from "@cloudscape-design/chat-components";
import { applyMode, Mode } from "@cloudscape-design/global-styles";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Activity, api, ExecutionProgress, runInference, TokenUsage } from "./api";
import { useAuth } from "./auth";
import { repositoryLink } from "./project";
import { DataAttribution } from "./data-attribution";
const ResponseExecution = lazy(() => import("./review"));

type Variant = "base" | "enriched";
type ChatMode = Variant | "ab";
type ChatResponse = { question: string; answer: string; status: string; mode?: Variant; latency_ms?: number; usage?: Record<string, number>; job_id?: string; activity?: Activity[] };
type Turn = ChatResponse & { id: string; comparison?: Partial<Record<Variant, ChatResponse>> };
type Thread = { id: string; title: string; turns: Turn[]; mode: ChatMode };
const labels: Record<ChatMode, string> = { base: "No enhancement", enriched: "Enhanced", ab: "A/B testing" };
const variants: Variant[] = ["base", "enriched"];
const variantOptions = variants.map(value => ({ value, label: labels[value] }));
const prompts = [
  "Compare rank 100 progression in events 1 and 2 by elapsed event day.",
  "Show the daily score evolution at rank 100 in event 1.",
  "Which characters have cards associated with event 1?",
];
const newThread = (mode: ChatMode = "enriched"): Thread => ({ id: crypto.randomUUID(), title: mode === "ab" ? "New A/B comparison" : `New ${labels[mode].toLowerCase()} chat`, turns: [], mode });
function loadThreads(key: string): Thread[] {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "[]");
    if (Array.isArray(value) && value.length && value.every(t => typeof t.id === "string" && typeof t.title === "string" && Array.isArray(t.turns))) return value.slice(0, 20).map(t => ({ ...t, mode: ["base", "enriched", "ab"].includes(t.mode) ? t.mode : "enriched" }));
  } catch { /* Use an empty conversation when browser storage is unavailable. */ }
  return [newThread()];
}

function ResponseContent({ response }: { response: ChatResponse }) {
  return <SpaceBetween size="m">
    {response.status !== "ok" && <StatusIndicator type={response.status === "unsupported" ? "info" : "error"}>{response.status === "unsupported" ? "Available data is limited" : response.status === "limited" ? "Token allowance reached" : "Unable to complete this response"}</StatusIndicator>}
    <TextContent><ReactMarkdown remarkPlugins={[remarkGfm]}>{response.answer}</ReactMarkdown></TextContent>
  </SpaceBetween>;
}

function AssistantMessage({ response, running = false, progress, reviewMode, label = "Assistant", actions }: {
  response?: ChatResponse; running?: boolean; progress?: ExecutionProgress; reviewMode: boolean; label?: string; actions?: ReactNode;
}) {
  const bubble = (evidence: ReactNode) => <ChatBubble type="incoming" ariaLabel={`${label} response`} avatar={<Avatar color="gen-ai" iconName="gen-ai" loading={running} ariaLabel={running ? "Assistant loading" : "Generative AI assistant"} />} showLoadingBar={running}>
    <SpaceBetween size="m">
      {response ? <>
        <ResponseContent response={response} />
        {label !== "Assistant" && <KeyValuePairs columns={2} items={[
          { label: "Response time", value: response.latency_ms === undefined ? "Not reported" : `${(response.latency_ms / 1000).toFixed(1)} seconds` },
          { label: "Tokens", value: response.usage?.total_tokens?.toLocaleString() ?? "Not reported" },
        ]} />}
        {evidence}
        <SpaceBetween size="xs" direction="horizontal">
          <CopyToClipboard copyButtonAriaLabel={`Copy ${label === "Assistant" ? "response" : `${label.toLowerCase()} response`}`} copySuccessText="Response copied" copyErrorText="Could not copy response" textToCopy={response.answer} variant="icon" />
          {actions}
        </SpaceBetween>
      </> : <Box>Generating a response</Box>}
    </SpaceBetween>
  </ChatBubble>;
  return <Suspense fallback={bubble(null)}><ResponseExecution jobId={response?.job_id || progress?.job_id} activity={response?.activity || progress?.activity} reviewMode={reviewMode} running={running} label={label}>{bubble}</ResponseExecution></Suspense>;
}

function Comparison({ responses, running, progress, reviewMode }: { responses: Partial<Record<Variant, ChatResponse>>; running?: Variant; progress?: Partial<Record<Variant, ExecutionProgress>>; reviewMode: boolean }) {
  return <ColumnLayout columns={2} borders="vertical">
    {variants.map(variant => <section key={variant} aria-label={`${labels[variant]} response`}><SpaceBetween size="m">
      <Header variant="h3">{labels[variant]}</Header>
      {responses[variant] || running === variant ? <AssistantMessage response={responses[variant]} running={!responses[variant] && running === variant} progress={progress?.[variant]} reviewMode={reviewMode} label={labels[variant]} /> : <Box color="text-body-secondary">Waiting to run</Box>}
    </SpaceBetween></section>)}
  </ColumnLayout>;
}

export function UserChat() {
  const { user, signOut } = useAuth();
  const storageKey = `puroseka.chat.${user.username}`;
  const [threads, setThreads] = useState<Thread[]>(() => loadThreads(storageKey));
  const [active, setActive] = useState(threads[0].id);
  const [chatToDelete, setChatToDelete] = useState<Thread | null>(null);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState("");
  const [pendingThread, setPendingThread] = useState("");
  const [pendingResults, setPendingResults] = useState<Partial<Record<Variant, ChatResponse>>>({});
  const [pendingProgress, setPendingProgress] = useState<Partial<Record<Variant, ExecutionProgress>>>({});
  const [pendingVariant, setPendingVariant] = useState<Variant>("base");
  const [preferredVariant, setPreferredVariant] = useState<Variant>("enriched");
  const [reviewMode, setReviewMode] = useState(user.role === "admin" && localStorage.getItem(`puroseka.review.${user.username}`) === "1");
  const [quota, setQuota] = useState<TokenUsage | null>(null);
  const [dark, setDark] = useState(localStorage.getItem("scl.darkMode") === "1");
  const [announcement, setAnnouncement] = useState("");
  const log = useRef<HTMLDivElement>(null);
  const workspace = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const current = threads.find(t => t.id === active) || threads[0];
  const ab = current.mode === "ab";
  useEffect(() => { if (current.mode !== "ab") setPreferredVariant(current.mode); }, [current.mode]);
  useEffect(() => { localStorage.setItem(`puroseka.review.${user.username}`, reviewMode ? "1" : "0"); }, [reviewMode, user.username]);
  useEffect(() => { applyMode(dark ? Mode.Dark : Mode.Light); localStorage.setItem("scl.darkMode", dark ? "1" : "0"); }, [dark]);
  useEffect(() => { try { localStorage.setItem(storageKey, JSON.stringify(threads)); } catch { /* Keep the conversation in memory. */ } }, [threads, storageKey]);
  useEffect(() => { api<TokenUsage>("/usage").then(setQuota).catch(() => {}); }, []);
  useEffect(() => {
    const element = workspace.current;
    if (!element) return;
    const fit = () => {
      const offset = element.getBoundingClientRect().top + window.scrollY;
      element.style.setProperty("--chat-top-offset", `${offset}px`);
    };
    const observer = new ResizeObserver(fit);
    observer.observe(element);
    const header = document.getElementById("chat-top-navigation");
    if (header) observer.observe(header);
    window.addEventListener("resize", fit);
    fit();
    return () => { observer.disconnect(); window.removeEventListener("resize", fit); };
  }, []);
  useEffect(() => { if (log.current && stickToBottom.current) log.current.scrollTop = log.current.scrollHeight; }, [threads, busy, active, pendingResults, pendingProgress]);
  async function send(value = question.trim(), regenerate = false) {
    if (!value || busy || value.length > 2000) return;
    const threadId = current.id;
    const previous = regenerate ? current.turns.slice(0, -1) : current.turns;
    const history = ab ? [] : previous.filter(t => t.status === "ok").slice(-3).flatMap(t => [{ role: "user", content: t.question }, { role: "assistant", content: t.answer.slice(0, 10000) }]);
    setBusy(true); setPending(value); setPendingThread(threadId); setPendingResults({}); setPendingProgress({}); setPendingVariant(ab ? "base" : current.mode as Variant); setQuestion(""); setAnnouncement(ab ? "Comparing both variants" : "Generating a response"); stickToBottom.current = true;
    if (regenerate) setThreads(all => all.map(t => t.id === threadId ? { ...t, turns: previous } : t));
    const results: Partial<Record<Variant, ChatResponse>> = {};
    for (const variant of ab ? variants : [current.mode as Variant]) {
      setPendingVariant(variant);
      try {
        const response = await runInference<ChatResponse>("/chat/jobs", { question: value, mode: variant, history }, progress => setPendingProgress(all => ({ ...all, [variant]: progress })));
        results[variant] = { ...response, question: value };
      } catch (e) { results[variant] = { question: value, answer: (e as Error).message, status: "error", mode: variant }; }
      setPendingResults({ ...results });
    }
    const result: Turn = ab ? {
      id: crypto.randomUUID(), question: value, answer: "A/B comparison", status: variants.every(v => results[v]?.status === "ok") ? "ok" : "error", comparison: results,
    } : { ...results[current.mode as Variant]!, question: value, id: crypto.randomUUID() };
    setThreads(all => all.map(t => t.id === threadId ? { ...t, title: t.turns.length ? t.title : `${labels[t.mode]} · ${value.slice(0, 48)}`, turns: [...t.turns.slice(-19), result] } : t));
    setBusy(false); setPending(""); setAnnouncement(result.status === "ok" ? "Response ready" : "Response completed with a notice");
    api<TokenUsage>("/usage").then(setQuota).catch(() => {});
  }
  function startNew(mode: ChatMode = current.mode) {
    const thread = newThread(mode); setThreads(all => [thread, ...all].slice(0, 20)); setActive(thread.id); setQuestion(""); stickToBottom.current = true;
  }
  function deleteChat() {
    if (!chatToDelete || busy) return;
    const remaining = threads.filter(thread => thread.id !== chatToDelete.id);
    if (!remaining.length) remaining.push(newThread(chatToDelete.mode));
    setThreads(remaining);
    if (active === chatToDelete.id) { setActive(remaining[0].id); setQuestion(""); stickToBottom.current = true; }
    setChatToDelete(null); setAnnouncement("Chat deleted");
  }
  return <div className="chat-page">
    <div id="chat-top-navigation"><TopNavigation identity={{ href: "/", title: "Puroseka" }} utilities={[
      { type: "button", text: dark ? "Light mode" : "Dark mode", onClick: () => setDark(!dark) },
      { type: "menu-dropdown", text: user.username, items: [
        ...(user.role === "admin" ? [{ id: "admin", text: "COA administration", href: "/admin/sources" }] : []),
        { id: "logout", text: "Sign out" },
      ], onItemClick: ({ detail }) => { if (detail.id === "logout") void signOut(); } },
      repositoryLink,
    ]} /></div>
    <AppLayout headerSelector="#chat-top-navigation" toolsHide disableContentPaddings
      ariaLabels={{ navigation: "Chat settings and conversations", navigationToggle: "Toggle chat sidebar", navigationClose: "Close chat sidebar" }}
      navigation={<SpaceBetween size="l"><Box padding={{ top: "l", horizontal: "l" }}><SpaceBetween size="m">
        <Button fullWidth iconName="add-plus" disabled={busy} onClick={() => startNew()}>New chat</Button>
        <Button fullWidth iconName="remove" disabled={busy} onClick={() => setChatToDelete(current)}>Delete chat</Button>
        <Header variant="h3">Chat settings</Header>
        <FormField label="Response agent" description="Changing the agent opens a new chat."><Select selectedOption={variantOptions.find(v => v.value === (ab ? preferredVariant : current.mode))!} options={variantOptions} disabled={busy || ab} onChange={({ detail }) => startNew(detail.selectedOption.value as Variant)} /></FormField>
        <Toggle checked={ab} disabled={busy} onChange={({ detail }) => startNew(detail.checked ? "ab" : preferredVariant)}>A/B testing</Toggle>
        {user.role === "admin" && <FormField description="Inspect recorded calls and JSON."><Toggle checked={reviewMode} onChange={({ detail }) => setReviewMode(detail.checked)}>Review mode</Toggle></FormField>}
        {ab && <Box color="text-body-secondary" fontSize="body-s">Compare both agents with the same model and question, without conversation history.</Box>}
      </SpaceBetween></Box><SideNavigation
        header={{ text: "Recent chats", href: "/" }} activeHref={`#${active}`}
        onFollow={(event) => { event.preventDefault(); const id = event.detail.href.slice(1); if (threads.some(t => t.id === id)) { setActive(id); stickToBottom.current = true; } }}
        items={threads.map(t => ({ type: "link", text: t.title, href: `#${t.id}` }))}
      /><Box padding={{ horizontal: "l", bottom: "l" }}><DataAttribution /></Box></SpaceBetween>}
      content={<div className="chat-workspace" ref={workspace}>
        <div className="chat-heading"><Header variant="h1" description="Explore event rankings, compare progress, and discover connections.">Puroseka assistant</Header></div>
        <div className="conversation-log" ref={log} role="region" aria-label="Chat" onScroll={() => {
          if (log.current) stickToBottom.current = log.current.scrollHeight - log.current.scrollTop - log.current.clientHeight < 100;
        }}>
          <div className="conversation-width"><SpaceBetween size="l">
            {!current.turns.length && !(busy && pendingThread === current.id) && <Box padding={{ vertical: "xxl" }}>
              <SpaceBetween size="l">
                <Avatar color="gen-ai" iconName="gen-ai" ariaLabel="Generative AI assistant" />
                <Header variant="h2" description="Ask about historical events, score changes, cards, or characters.">What would you like to explore?</Header>
                <SupportPromptGroup ariaLabel="Suggested questions" items={prompts.map((text, i) => ({ id: String(i), text, iconName: "gen-ai" }))} onItemClick={({ detail }) => setQuestion(prompts[Number(detail.id)])} />
              </SpaceBetween>
            </Box>}
            {current.turns.map((turn, index) => <SpaceBetween key={turn.id} size="m">
              <ChatBubble type="outgoing" ariaLabel={`You: ${turn.question}`} avatar={<Avatar initials={user.username[0].toUpperCase()} ariaLabel="You" />}><Box>{turn.question}</Box></ChatBubble>
              {turn.comparison ? <SpaceBetween size="m">
                <Comparison responses={turn.comparison} reviewMode={reviewMode} />
                {index === current.turns.length - 1 && <Button variant="inline-link" iconName="refresh" disabled={busy} onClick={() => void send(turn.question, true)}>Regenerate comparison</Button>}
              </SpaceBetween> : <AssistantMessage response={turn} reviewMode={reviewMode} actions={index === current.turns.length - 1 && <Button variant="inline-link" iconName="refresh" disabled={busy} onClick={() => void send(turn.question, true)}>{turn.status === "ok" ? "Regenerate response" : "Try again"}</Button>} />}
            </SpaceBetween>)}
            {busy && pendingThread === current.id && <SpaceBetween size="m">
              <ChatBubble type="outgoing" ariaLabel={`You: ${pending}`} avatar={<Avatar initials={user.username[0].toUpperCase()} ariaLabel="You" />}><Box>{pending}</Box></ChatBubble>
              {current.mode === "ab" ? <Comparison responses={pendingResults} running={pendingVariant} progress={pendingProgress} reviewMode={reviewMode} /> : <AssistantMessage running progress={pendingProgress[current.mode as Variant]} reviewMode={reviewMode} />}
            </SpaceBetween>}
          </SpaceBetween></div>
        </div>
        <div className="chat-composer"><div className="conversation-width"><SpaceBetween size="s">
          {quota?.enabled && <Alert type={quota.remaining_tokens === 0 ? "warning" : "info"}>{quota.remaining_tokens.toLocaleString()} tokens available. Allowance resets {new Date(quota.resets_at).toLocaleTimeString()}.</Alert>}
          <PromptInput value={question} onChange={({ detail }) => setQuestion(detail.value)} onAction={() => void send()}
            ariaLabel="Ask a question" placeholder="Ask a question" minRows={2} maxRows={6} disabled={busy}
            actionButtonIconName="send" disableActionButton={!question.trim() || busy || question.length > 2000}
            i18nStrings={{ actionButtonAriaLabel: "Send message" }} />
          <Box color="text-body-secondary" fontSize="body-s" textAlign="center">Responses are generated by AI from historical data. Check important details. {question.length > 2000 && "Keep your question under 2,000 characters."}</Box>
        </SpaceBetween></div></div>
        <LiveRegion hidden>{announcement}</LiveRegion>
      </div>}
    />
    <Modal visible={!!chatToDelete} onDismiss={() => setChatToDelete(null)} header="Delete chat" closeAriaLabel="Close delete chat dialog" footer={
      <SpaceBetween direction="horizontal" size="xs">
        <Button variant="link" onClick={() => setChatToDelete(null)}>Cancel</Button>
        <Button variant="primary" disabled={busy} onClick={deleteChat}>Delete chat</Button>
      </SpaceBetween>
    }>
      Delete “{chatToDelete?.title}”? This removes the conversation and its responses from this browser.
    </Modal>
  </div>;
}
