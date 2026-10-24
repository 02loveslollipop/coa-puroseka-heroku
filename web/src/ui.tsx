// SPDX-License-Identifier: Apache-2.0
import type { ReactNode } from "react";
import {
  Alert,
  Box,
  CopyToClipboard,
  SpaceBetween,
  Spinner,
  Table,
} from "@cloudscape-design/components";
import { CodeView } from "@cloudscape-design/code-view";

export function LoadingState({ label }: { label: string }) {
  return (
    <Box padding="l">
      <SpaceBetween direction="horizontal" size="s">
        <Spinner />
        <Box>{label}</Box>
      </SpaceBetween>
    </Box>
  );
}
export function DataRows({
  rows,
  columns,
  loading = false,
  header,
  pagination,
  error,
}: {
  rows: Record<string, unknown>[];
  columns?: string[];
  loading?: boolean;
  header?: ReactNode;
  pagination?: ReactNode;
  error?: string;
}) {
  const cols = columns || Object.keys(rows[0] || {});
  return (
    <Table<Record<string, unknown>>
      variant="embedded"
      wrapLines
      resizableColumns
      ariaLabels={{ tableLabel: "Database records" }}
      header={header}
      pagination={pagination}
      items={rows}
      loading={loading}
      loadingText="Loading database records"
      columnDefinitions={cols.map((key) => ({
        id: key,
        header: key,
        minWidth: 150,
        cell: (row) =>
          row[key] === null || row[key] === undefined
            ? "—"
            : typeof row[key] === "object"
              ? JSON.stringify(row[key])
              : String(row[key]),
      }))}
      empty={
        error ? (
          <Alert type="error" header="Could not load records">
            {error}
          </Alert>
        ) : (
          <Box textAlign="center" padding="l">
            No matching records.
          </Box>
        )
      }
    />
  );
}
export function FieldDefinitions({
  fields,
}: {
  fields: Record<string, { type: string; definition: string }>;
}) {
  return (
    <Table
      variant="embedded"
      ariaLabels={{ tableLabel: "Field definitions" }}
      wrapLines
      items={Object.entries(fields).map(([field, value]) => ({
        field,
        ...value,
      }))}
      columnDefinitions={[
        {
          id: "field",
          header: "Field",
          minWidth: 200,
          cell: (item) => <Box variant="code">{item.field}</Box>,
        },
        {
          id: "type",
          header: "Type",
          minWidth: 140,
          cell: (item) => item.type,
        },
        {
          id: "meaning",
          header: "Definition",
          cell: (item) => item.definition,
        },
      ]}
    />
  );
}
export function CodeSnippet({
  content,
  label,
}: {
  content: string;
  label: string;
}) {
  return (
    <div className="code-viewport">
      <CodeView
        content={content}
        ariaLabel={label}
        lineNumbers
        wrapLines
        i18nStrings={{ lineNumberLabel: "Line number", codeLabel: "Code" }}
        actions={
          <CopyToClipboard
            variant="icon"
            textToCopy={content}
            copyButtonAriaLabel={`Copy ${label.toLowerCase()}`}
            copySuccessText="Copied"
            copyErrorText="Copy failed"
          />
        }
      />
    </div>
  );
}
