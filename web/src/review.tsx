import { ReactNode, useEffect, useState } from "react";
import { Alert, Box, Button, ExpandableSection, SpaceBetween, Spinner, Steps, Tabs } from "@cloudscape-design/components";
import type { StatusIndicatorProps } from "@cloudscape-design/components";
import { Activity, api } from "./api";
import { CodeSnippet, DataRows } from "./ui";

type Call = Activity & { input: unknown; output: unknown };
type Review = { job_id: string; input: unknown; output: unknown; calls: Call[] };
type Evidence = { sql?: string; rows?: Record<string, unknown>[]; truncated?: boolean };
const json = (value: unknown) => JSON.stringify(value, null, 2);
const status = (value: string): StatusIndicatorProps.Type => value === "running" ? "loading" : value === "ok" ? "success" : value === "blocked" ? "stopped" : "error";
const names: Record<string, string> = { physical_schema: "Read database schema", ontology_context: "Read ontology context", execute_query: "Query database", "Generate query": "Generate SQL", "Synthesize response": "Write response" };

function CallDetails({ call }: { call: Call }) {
  const [expanded, setExpanded] = useState(false);
  const [tab, setTab] = useState("input");
  return <ExpandableSection headerText={`Review ${call.kind} call: ${call.name}`} expanded={expanded} onChange={({ detail }) => setExpanded(detail.expanded)}>
    <Tabs activeTabId={tab} onChange={({ detail }) => setTab(detail.activeTabId)} tabs={[
      { id: "input", label: "Input JSON", content: <CodeSnippet label={`${call.name} input JSON`} content={json(call.input)} /> },
      { id: "output", label: "Output JSON", content: <CodeSnippet label={`${call.name} output JSON`} content={json(call.output)} /> },
    ]} />
  </ExpandableSection>;
}

export default function ResponseExecution({ jobId, activity, reviewMode, running = false, label = "Assistant", children }: {
  jobId?: string; activity?: Activity[]; reviewMode: boolean; running?: boolean; label?: string;
  children: (evidence: ReactNode) => ReactNode;
}) {
  const [expanded, setExpanded] = useState(running);
  const [data, setData] = useState<Review | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [detailsTab, setDetailsTab] = useState("input");
  useEffect(() => { setExpanded(running); }, [running]);
  useEffect(() => {
    if (!reviewMode || !jobId || running) return;
    let active = true;
    setLoading(true); setError(""); setData(null);
    api<Review>(`/jobs/${jobId}/review`).then(value => { if (active) setData(value); }).catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [reviewMode, jobId, running, attempt]);
  const calls = data?.calls || activity || [];
  const tools = calls.filter(call => call.kind === "tool").length;
  const current = calls.find(call => call.status === "running");
  const failed = calls.some(call => ["error", "blocked"].includes(call.status));
  const query = data?.calls.find(call => call.name === "execute_query" && call.status === "ok");
  const evidence = query?.output as Evidence | undefined;
  return <SpaceBetween size="m">
    <section className="assistant-activity" aria-label={`${label} tool activity`}>
      <ExpandableSection headerText="Tool activity" expanded={expanded} onChange={({ detail }) => setExpanded(detail.expanded)} headerDescription={
        running ? current ? `${names[current.name] || current.name} · In progress` : "Waiting for execution" : `${tools} tool ${tools === 1 ? "call" : "calls"} · ${failed ? "Completed with an error" : "Finished"}`
      }>
        <SpaceBetween size="m">
          {!!calls.length && <Steps ariaLabel={`${label} execution steps`} steps={calls.map(call => ({
            status: status(call.status), statusIconAriaLabel: call.status === "ok" ? "Completed" : call.status,
            header: <SpaceBetween direction="horizontal" size="s"><Box>{names[call.name] || call.name}</Box><Box color="text-body-secondary" fontSize="body-s">{call.kind === "tool" ? call.name : "Model call"}{call.latency_ms !== undefined && ` · ${(call.latency_ms / 1000).toFixed(2)} s`}</Box></SpaceBetween>,
            details: reviewMode && data ? <CallDetails key={call.sequence} call={data.calls.find(c => c.sequence === call.sequence)!} /> : undefined,
          }))} />}
          {!calls.length && <Box color="text-body-secondary">{running ? "Your request is queued. Execution steps will appear as they run." : "No execution steps are available for this earlier response."}</Box>}
          {reviewMode && <>
            {loading && <Box><Spinner /> Loading recorded call JSON</Box>}
            {error && <Alert type="error" action={<Button onClick={() => setAttempt(value => value + 1)}>Retry</Button>}>{error}</Alert>}
            {!jobId && !running && <Box color="text-body-secondary">Regenerate this response to capture its review data.</Box>}
            {data && <ExpandableSection headerText="Review request and result JSON">
              <Tabs activeTabId={detailsTab} onChange={({ detail }) => setDetailsTab(detail.activeTabId)} tabs={[
                { id: "input", label: "Input JSON", content: <CodeSnippet label="Inference input JSON" content={json(data.input)} /> },
                { id: "output", label: "Output JSON", content: <CodeSnippet label="Inference output JSON" content={json(data.output)} /> },
              ]} />
            </ExpandableSection>}
          </>}
        </SpaceBetween>
      </ExpandableSection>
    </section>
    {children(reviewMode && evidence ? <SpaceBetween size="m">
      <ExpandableSection headerText="Executed SQL"><CodeSnippet label="Executed SQL" content={evidence.sql || ""} /></ExpandableSection>
      <ExpandableSection headerText={`SQL results (${evidence.rows?.length || 0} ${evidence.rows?.length === 1 ? "row" : "rows"})`}>
        <SpaceBetween size="s">
          {evidence.truncated && <Alert type="info">Results were limited to the query evidence cap. The answer uses these returned rows.</Alert>}
          <DataRows rows={evidence.rows || []} />
          <ExpandableSection headerText="Results JSON"><CodeSnippet label="SQL results JSON" content={json(query?.output)} /></ExpandableSection>
        </SpaceBetween>
      </ExpandableSection>
    </SpaceBetween> : null)}
  </SpaceBetween>;
}
