// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
// Native Cloudscape generative-AI controls and evidence inspection.
import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  ColumnLayout,
  Container,
  ContentLayout,
  ExpandableSection,
  FormField,
  Header,
  KeyValuePairs,
  PromptInput,
  SegmentedControl,
  SpaceBetween,
  StatusIndicator,
  Tabs,
  TextContent,
} from "@cloudscape-design/components";
import {
  Avatar,
  ChatBubble,
  SupportPromptGroup,
} from "@cloudscape-design/chat-components";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, Catalog, Run } from "./api";
import { CodeSnippet, DataRows } from "./ui";

const examples = [
  "Compare the last observed competition score at rank 100 for events 1 and 2.",
  "Show the daily score evolution at rank 100 in event 1.",
  "Compare rank 100 progression in events 1 and 2 by elapsed event day.",
  "Which characters have cards associated with event 1?",
  "How many distinct cards are listed in each of the first five gachas?",
  "When did event 1 finish its competition period, in UTC?",
  "What is the current score needed to reach rank 100?",
];
const dump = (value: unknown) => JSON.stringify(value, null, 2);
function readHistory(): Record<string, Run[]> {
  try {
    const saved = JSON.parse(
      localStorage.getItem("puroseka.histories") || "{}",
    );
    return {
      base: Array.isArray(saved.base) ? saved.base : [],
      enriched: Array.isArray(saved.enriched) ? saved.enriched : [],
    };
  } catch {
    return { base: [], enriched: [] };
  }
}
export function Playground({ catalog }: { catalog: Catalog | null }) {
  const [mode, setMode] = useState("enriched");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [histories, setHistories] =
    useState<Record<string, Run[]>>(readHistory);
  const [comparison, setComparison] = useState(false);
  const [health, setHealth] = useState<{
    model: string;
    mistral_configured: boolean;
  } | null>(null);
  useEffect(() => {
    api<{ model: string; mistral_configured: boolean }>("/health")
      .then(setHealth)
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem("puroseka.histories", dump(histories));
    } catch {
      /* History remains in memory if browser storage is full. */
    }
  }, [histories]);
  async function send(both = false) {
    if (!question.trim() || busy) return;
    const value = question.trim();
    setBusy(true);
    setError("");
    setComparison(both);
    try {
      for (const currentMode of both ? ["base", "enriched"] : [mode]) {
        const history = both
          ? []
          : (histories[currentMode] || [])
              .filter((run) => run.status === "ok")
              .slice(-3)
              .flatMap((run) => [
                { role: "user", content: run.question },
                { role: "assistant", content: run.answer },
              ]);
        const run = await api<Run>("/inference", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: value, mode: currentMode, history }),
        });
        setHistories((previous) => ({
          ...previous,
          [currentMode]: [...(previous[currentMode] || []).slice(-9), run],
        }));
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const displayed = comparison ? ["base", "enriched"] : [mode];
  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Compare physical schema alone with ontology-enriched context and NL2SQL examples."
          actions={
            <Button
              disabled={busy}
              onClick={() => {
                setHistories({ base: [], enriched: [] });
                setComparison(false);
              }}
            >
              New chat
            </Button>
          }
        >
          Inference playground
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Container header={<Header variant="h2">Agent configuration</Header>}>
          <SpaceBetween size="l">
            <ColumnLayout columns={3}>
              <FormField label="Context mode">
                <SegmentedControl
                  label="Context mode"
                  selectedId={mode}
                  onChange={({ detail }) => {
                    setMode(detail.selectedId);
                    setComparison(false);
                  }}
                  options={[
                    { id: "base", text: "Base", disabled: busy },
                    {
                      id: "enriched",
                      text: "Semantic-enriched",
                      disabled: busy,
                    },
                  ]}
                />
              </FormField>
              <KeyValuePairs
                items={[
                  {
                    label: "Mistral model",
                    value: health?.model || "Checking model",
                  },
                ]}
              />
              <KeyValuePairs
                items={[
                  {
                    label: "Semantic version",
                    value:
                      mode === "enriched"
                        ? catalog?.version || "Loading"
                        : "Physical schema only",
                  },
                ]}
              />
            </ColumnLayout>
            <Box color="text-body-secondary">
              Both agents use the same model and query tools. Enriched adds
              ontology definitions and domain NL2SQL examples. Comparisons start
              with empty histories; individual chats keep separate histories.
            </Box>
            {health && !health.mistral_configured && (
              <Alert type="warning">
                Mistral is not configured. Source and ontology browsing remain
                available.
              </Alert>
            )}
          </SpaceBetween>
        </Container>
        <Container header={<Header variant="h2">Ask the database</Header>}>
          <SpaceBetween size="l">
            <FormField
              label="Question"
              description="Ask about event comparisons, daily score changes, or relationships between source entities."
            >
              <PromptInput
                value={question}
                onChange={({ detail }) => setQuestion(detail.value)}
                onAction={() => void send()}
                disabled={busy}
                disableActionButton={!question.trim() || busy}
                minRows={3}
                maxRows={6}
                ariaLabel="Question"
                placeholder="For events 3 and 4 at rank 1000, compare progression by elapsed event day…"
                actionButtonIconName="send"
                i18nStrings={{
                  actionButtonAriaLabel: `Run ${mode === "base" ? "base" : "enriched"} agent`,
                }}
                secondaryActions={
                  <Button
                    disabled={busy || !question.trim()}
                    onClick={() => void send(true)}
                  >
                    Compare both
                  </Button>
                }
              />
            </FormField>
            <ExpandableSection headerText="Example questions">
              <SupportPromptGroup
                ariaLabel="Example database questions"
                items={examples.map((text, index) => ({
                  id: String(index),
                  text,
                  iconName: "gen-ai",
                }))}
                onItemClick={({ detail }) =>
                  setQuestion(examples[Number(detail.id)])
                }
              />
            </ExpandableSection>
          </SpaceBetween>
        </Container>
        {error && (
          <Alert type="error" header="Request failed">
            {error}
          </Alert>
        )}
        {busy && (
          <ChatBubble
            type="incoming"
            ariaLabel="Agent is querying the database"
            avatar={
              <Avatar
                color="gen-ai"
                iconName="gen-ai"
                loading
                ariaLabel="Agent loading"
              />
            }
            showLoadingBar
          >
            <Box>
              Querying the database and checking the available evidence. Sandbox
              rate limits can add a short delay.
            </Box>
          </ChatBubble>
        )}
        <ColumnLayout columns={comparison ? 2 : 1}>
          {displayed.map((currentMode) => (
            <SpaceBetween key={currentMode} size="l">
              <Header variant="h2">
                {currentMode === "base"
                  ? "Base agent"
                  : "Semantic-enriched agent"}
              </Header>
              {(comparison
                ? (histories[currentMode] || []).slice(-1)
                : histories[currentMode] || []
              ).map((run, index) => (
                <RunView key={index} run={run} />
              ))}
              {!histories[currentMode]?.length && (
                <Container>
                  <Box textAlign="center" padding="l">
                    <SpaceBetween size="s">
                      <Box variant="strong">Start a conversation</Box>
                      <Box color="text-body-secondary">
                        Ask a question to see its answer, SQL, evidence, and
                        supplied context.
                      </Box>
                    </SpaceBetween>
                  </Box>
                </Container>
              )}
            </SpaceBetween>
          ))}
        </ColumnLayout>
      </SpaceBetween>
    </ContentLayout>
  );
}
function RunView({ run }: { run: Run }) {
  return (
    <Container>
      <SpaceBetween size="l">
        <ChatBubble
          type="outgoing"
          align="end"
          ariaLabel={`Your question: ${run.question}`}
          avatar={<Avatar ariaLabel="You" initials="U" />}
        >
          <Box>{run.question}</Box>
        </ChatBubble>
        <ChatBubble
          type="incoming"
          ariaLabel={`${run.mode} agent answer`}
          avatar={
            <Avatar
              color="gen-ai"
              iconName="gen-ai"
              ariaLabel={`${run.mode} agent`}
            />
          }
          stretch
        >
          <SpaceBetween size="m">
            <StatusIndicator
              type={
                run.status === "ok"
                  ? "success"
                  : run.status === "unsupported"
                    ? "info"
                    : "error"
              }
            >
              {run.status === "ok"
                ? "Query completed"
                : run.status === "unsupported"
                  ? "Unsupported question"
                  : "Query failed"}
            </StatusIndicator>
            <TextContent>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {run.answer}
              </ReactMarkdown>
            </TextContent>
          </SpaceBetween>
        </ChatBubble>
        <KeyValuePairs
          columns={3}
          items={[
            {
              label: "Response time",
              value: `${(run.latency_ms / 1000).toFixed(1)} seconds`,
            },
            {
              label: "Tokens",
              value: run.usage.total_tokens?.toLocaleString() || "Not reported",
            },
            { label: "Model", value: run.model },
          ]}
        />
        {run.truncated && (
          <Alert type="warning">
            Evidence was limited by the row or response-size cap. Inspect the
            returned records before drawing conclusions.
          </Alert>
        )}
        <Tabs
          tabs={[
            {
              id: "evidence",
              label: `Evidence (${run.rows.length})`,
              content: (
                <SpaceBetween size="m">
                  {run.query_purpose && <Box>{run.query_purpose}</Box>}
                  {run.sql && (
                    <CodeSnippet content={run.sql} label="Generated SQL" />
                  )}
                  <DataRows rows={run.rows} />
                </SpaceBetween>
              ),
            },
            {
              id: "context",
              label: "Supplied context",
              content: (
                <CodeSnippet
                  content={dump(run.context)}
                  label="Supplied semantic context"
                />
              ),
            },
            {
              id: "trace",
              label: "Request trace",
              content: (
                <CodeSnippet
                  content={dump({
                    query_messages: run.query_messages,
                    answer_messages: run.answer_messages,
                    usage: run.usage,
                    semantic_version: run.semantic_version,
                  })}
                  label="Inference request trace"
                />
              ),
            },
          ]}
        />
      </SpaceBetween>
    </Container>
  );
}
