// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
// Uses Cloudscape's table-view, details-with-tabs and chart component patterns.
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  ColumnLayout,
  Container,
  ContentLayout,
  FormField,
  Header,
  Input,
  KeyValuePairs,
  LineChart,
  Pagination,
  SpaceBetween,
  Spinner,
  Table,
  Tabs,
  TextFilter,
} from "@cloudscape-design/components";
import { useCollection } from "@cloudscape-design/collection-hooks";
import { api, Catalog } from "./api";
import { DataRows, FieldDefinitions, LoadingState } from "./ui";

type Preview = { rows: Record<string, unknown>[]; columns: string[] };

export function Sources({ catalog }: { catalog: Catalog | null }) {
  const [selected, setSelected] = useState("events");
  const [offset, setOffset] = useState(0);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const allItems = useMemo(
    () =>
      Object.entries(catalog?.tables || {}).map(([table, entity]) => ({
        table,
        ...entity,
      })),
    [catalog],
  );
  const {
    items,
    collectionProps,
    filterProps,
    paginationProps,
    filteredItemsCount,
  } = useCollection(allItems, {
    filtering: {
      empty: (
        <Box padding="l" textAlign="center">
          No source tables available.
        </Box>
      ),
      noMatch: (
        <Box padding="l" textAlign="center">
          No tables match your filter.
        </Box>
      ),
    },
    sorting: { defaultState: { sortingColumn: { sortingField: "table" } } },
    pagination: { pageSize: 10 },
  });
  useEffect(() => {
    let active = true;
    setPreview(null);
    setError("");
    api<Preview>(`/tables/${selected}?offset=${offset}`)
      .then((data) => {
        if (active) setPreview(data);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [selected, offset, refresh]);
  if (!catalog) return <LoadingState label="Loading data sources" />;
  const entity = catalog.tables[selected];
  function select(table: string) {
    setSelected(table);
    setOffset(0);
  }
  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Inspect source tables, their semantic definitions, and historical ranking data."
        >
          Data sources
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Container
          header={
            <Header
              variant="h2"
              description={`Published semantic version ${catalog.version}`}
            >
              Dataset overview
            </Header>
          }
        >
          <ColumnLayout columns={3} variant="text-grid" borders="vertical">
            <div>
              <Box variant="awsui-key-label">Source files</Box>
              <Box variant="awsui-value-large">
                {(5 + (catalog.ranking_source_files || 0)).toLocaleString()}
              </Box>
              <Box color="text-body-secondary">
                Metadata and ranking history
              </Box>
            </div>
            <div>
              <Box variant="awsui-key-label">Relational tables</Box>
              <Box variant="awsui-value-large">{allItems.length}</Box>
              <Box color="text-body-secondary">
                Entities and nested collections
              </Box>
            </div>
            <div>
              <Box variant="awsui-key-label">Ranking observations</Box>
              <Box variant="awsui-value-large">
                {catalog.tables.ranking_observations?.row_count.toLocaleString() ||
                  "Preparing"}
              </Box>
              <Box color="text-body-secondary">
                {catalog.data_coverage?.ranking_events_with_observations || "…"}{" "}
                events with observations / 180 metadata events
              </Box>
            </div>
          </ColumnLayout>
        </Container>
        <Table
          {...collectionProps}
          ariaLabels={{
            tableLabel: "Source tables",
            selectionGroupLabel: "Select a source table",
            itemSelectionLabel: (_, item) => `Select ${item.table}`,
          }}
          header={
            <Header
              variant="h2"
              counter={`(${filteredItemsCount ?? allItems.length})`}
              description="Select a table to inspect its records and field definitions."
            >
              Source tables
            </Header>
          }
          filter={
            <TextFilter
              {...filterProps}
              filteringPlaceholder="Find a table"
              filteringAriaLabel="Find a table"
              countText={`${filteredItemsCount ?? allItems.length} matches`}
            />
          }
          pagination={
            <Pagination
              {...paginationProps}
              ariaLabels={{
                nextPageLabel: "Next table page",
                previousPageLabel: "Previous table page",
                pageLabel: (page) => `Table page ${page}`,
              }}
            />
          }
          items={items}
          trackBy="table"
          selectionType="single"
          selectedItems={allItems.filter((item) => item.table === selected)}
          onSelectionChange={({ detail }) => {
            if (detail.selectedItems[0]) select(detail.selectedItems[0].table);
          }}
          columnDefinitions={[
            {
              id: "table",
              header: "Table",
              sortingField: "table",
              minWidth: 210,
              cell: (item) => (
                <Button
                  variant="inline-link"
                  onClick={() => select(item.table)}
                >
                  {item.table}
                </Button>
              ),
            },
            {
              id: "concept",
              header: "Concept",
              sortingField: "label",
              minWidth: 170,
              cell: (item) => item.label,
            },
            {
              id: "rows",
              header: "Rows",
              sortingField: "row_count",
              minWidth: 100,
              cell: (item) => item.row_count.toLocaleString(),
            },
            {
              id: "definition",
              header: "Definition",
              cell: (item) => item.definition,
            },
          ]}
          wrapLines
        />
        <Container
          header={
            <Header variant="h2" description={entity.definition}>
              {entity.label}
            </Header>
          }
        >
          <KeyValuePairs
            columns={3}
            items={[
              { label: "SQL table", value: `data.${selected}` },
              { label: "Ontology class", value: entity.class },
              { label: "Records", value: entity.row_count.toLocaleString() },
            ]}
          />
          <Box margin={{ top: "l" }}>
            <Tabs
              tabs={[
                {
                  id: "preview",
                  label: "Data preview",
                  content: (
                    <SpaceBetween size="m">
                      <Alert type="info">
                        Original records are preserved in raw_record. Japanese
                        source labels are retained; generated English
                        translations are approximate.
                      </Alert>
                      <DataRows
                        rows={preview?.rows || []}
                        columns={preview?.columns}
                        loading={!preview && !error}
                        header={
                          <Header
                            variant="h3"
                            counter={`(${entity.row_count.toLocaleString()})`}
                            actions={
                              <Button
                                iconName="refresh"
                                ariaLabel="Refresh data preview"
                                onClick={() => setRefresh((n) => n + 1)}
                              />
                            }
                          >
                            Records
                          </Header>
                        }
                        pagination={
                          <Pagination
                            currentPageIndex={Math.floor(offset / 25) + 1}
                            pagesCount={Math.max(
                              1,
                              Math.ceil(entity.row_count / 25),
                            )}
                            onChange={({ detail }) =>
                              setOffset((detail.currentPageIndex - 1) * 25)
                            }
                            ariaLabels={{
                              nextPageLabel: "Next records page",
                              previousPageLabel: "Previous records page",
                              pageLabel: (page) => `Records page ${page}`,
                            }}
                          />
                        }
                        error={error}
                      />
                    </SpaceBetween>
                  ),
                },
                {
                  id: "fields",
                  label: "Field definitions",
                  content: <FieldDefinitions fields={entity.fields} />,
                },
                ...(catalog.tables.ranking_observations
                  ? [
                      {
                        id: "ranking",
                        label: "Ranking history",
                        content: <RankingChart />,
                      },
                    ]
                  : []),
              ]}
            />
          </Box>
        </Container>
      </SpaceBetween>
    </ContentLayout>
  );
}

function RankingChart() {
  const [event, setEvent] = useState("1");
  const [rank, setRank] = useState("100");
  const [loaded, setLoaded] = useState({ event: "1", rank: "100" });
  const [points, setPoints] = useState<
    { observed_at: string; score: number }[]
  >([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [truncated, setTruncated] = useState(false);
  const valid = /^[1-9]\d*$/.test(event) && /^[1-9]\d*$/.test(rank);
  async function load() {
    if (!/^[1-9]\d*$/.test(event) || !/^[1-9]\d*$/.test(rank)) return;
    setLoading(true);
    setError("");
    setPoints([]);
    setTruncated(false);
    try {
      const result = await api<{
        rows: { observed_at: string; score: number }[];
        truncated: boolean;
      }>(`/rankings?event_id=${event}&rank=${rank}`);
      setPoints(result.rows);
      setTruncated(result.truncated);
      setLoaded({ event, rank });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, []);
  const data = points.map((point) => ({
    x: new Date(point.observed_at),
    y: point.score,
  }));
  return (
    <SpaceBetween size="l">
      <Header
        variant="h3"
        description="Historical scores occupying a rank. The player at that position can change."
      >
        Score over time
      </Header>
      <ColumnLayout columns={3}>
        <FormField label="Event ID" constraintText="Positive event identifier">
          <Input
            type="number"
            value={event}
            onChange={({ detail }) => setEvent(detail.value)}
            disabled={loading}
          />
        </FormField>
        <FormField label="Rank" constraintText="Positive leaderboard position">
          <Input
            type="number"
            value={rank}
            onChange={({ detail }) => setRank(detail.value)}
            disabled={loading}
          />
        </FormField>
        <FormField label="Series">
          <Button
            loading={loading}
            disabled={!valid}
            onClick={() => void load()}
          >
            Load series
          </Button>
        </FormField>
      </ColumnLayout>
      {truncated && (
        <Alert type="warning">
          Only the first 10,000 observations are plotted. All observations
          remain available through the database.
        </Alert>
      )}
      {error && (
        <Alert type="error" header="Could not load ranking history">
          {error}
        </Alert>
      )}
      <LineChart<Date>
        ariaLabel={`Historical scores for event ${loaded.event}, rank ${loaded.rank}`}
        ariaDescription="Use the arrow keys to explore recorded scores and their UTC observation timestamps. Gaps are unobserved intervals, not zero scores."
        height={320}
        series={
          data.length
            ? [
                {
                  title: `Event ${loaded.event} · rank ${loaded.rank}`,
                  type: "line",
                  data,
                  valueFormatter: (value) => `${value.toLocaleString()} points`,
                },
              ]
            : []
        }
        xScaleType="time"
        xTitle="Observation time (UTC)"
        yTitle="Event points"
        xTickFormatter={(date) =>
          date.toLocaleString("en-GB", {
            timeZone: "UTC",
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          })
        }
        yTickFormatter={(value) => value.toLocaleString()}
        statusType={loading ? "loading" : error ? "error" : "finished"}
        loadingText="Loading ranking observations"
        errorText="Ranking history could not be loaded."
        recoveryText="Retry"
        onRecoveryClick={() => void load()}
        empty={
          <Box textAlign="center" padding="l">
            <Box variant="strong">No recorded observations</Box>
            <Box color="text-body-secondary">
              Choose an event and rank with available history.
            </Box>
          </Box>
        }
        noMatch={
          <Box textAlign="center" padding="l">
            No series selected.
          </Box>
        }
      />
      <Box color="text-body-secondary">
        {points.length.toLocaleString()} recorded observations. Gaps are
        unobserved intervals, not zero scores. The histories are not live data.
      </Box>
    </SpaceBetween>
  );
}
