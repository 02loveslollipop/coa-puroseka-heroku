// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import {
  Box,
  Button,
  Container,
  ContentLayout,
  FormField,
  Header,
  KeyValuePairs,
  Select,
  SpaceBetween,
  StatusIndicator,
  Table,
  Tabs,
  TextContent,
} from "@cloudscape-design/components";
import * as tokens from "@cloudscape-design/design-tokens";
import { Background, Controls, MarkerType, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Catalog, apiUrl } from "./api";
import { FieldDefinitions, LoadingState } from "./ui";

export function Ontology({ catalog }: { catalog: Catalog | null }) {
  const [selected, setSelected] = useState("events");
  if (!catalog) return <LoadingState label="Loading ontology" />;
  const entity = catalog.tables[selected];
  const entries = Object.entries(catalog.tables);
  const positions: Record<string, [number, number]> = {
    events: [400, 40],
    event_cards: [670, 200],
    cards: [400, 360],
    characters: [400, 570],
    gachas: [40, 40],
    gacha_details: [40, 200],
    gacha_pickups: [40, 360],
    gacha_behaviors: [-220, 40],
    gacha_rarity_rates: [-220, 200],
    card_parameters: [670, 360],
    card_training_costs: [670, 510],
    card_master_resources: [670, 660],
    event_reward_ranges: [950, 40],
    event_rewards: [950, 200],
    entity_labels: [40, 570],
    ranking_observations: [400, -140],
  };
  const nodes = entries.map(([id, e], index) => ({
    id,
    data: {
      label: (
        <SpaceBetween size="xxs">
          <Box variant="strong">{e.label}</Box>
          <Box color="text-body-secondary" fontSize="body-s">
            {id}
          </Box>
        </SpaceBetween>
      ),
    },
    position: {
      x: positions[id]?.[0] ?? index * 190,
      y: positions[id]?.[1] ?? 700,
    },
    style: {
      width: 200,
      padding: tokens.spaceScaledM,
      color: tokens.colorTextBodyDefault,
      background: tokens.colorBackgroundContainerContent,
      borderColor:
        selected === id
          ? tokens.colorBorderItemFocused
          : tokens.colorBorderContainerTop,
      borderWidth: selected === id ? 2 : 1,
      borderRadius: tokens.borderRadiusContainer,
    },
  }));
  const edges = catalog.relationships.map((relation, index) => ({
    id: String(index),
    source: relation.source,
    target: relation.target,
    label: relation.name,
    markerEnd: { type: MarkerType.ArrowClosed },
    style: {
      stroke:
        relation.status === "validated"
          ? tokens.colorTextBodySecondary
          : tokens.colorTextStatusWarning,
    },
    labelStyle: { fontSize: 12, fill: tokens.colorTextBodyDefault },
    labelBgStyle: { fill: tokens.colorBackgroundContainerContent },
    labelBgPadding: [4, 3] as [number, number],
  }));
  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Explore source-grounded concepts, relationships, and database mappings."
          actions={
            <Button href={apiUrl("/ontology.ttl")} iconName="download">
              Download ontology
            </Button>
          }
        >
          Ontology explorer
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Container
          header={
            <Header
              variant="h2"
              description="Select a concept to inspect its definition and relationships."
            >
              Entity relationships
            </Header>
          }
        >
          <KeyValuePairs
            columns={3}
            items={[
              { label: "Semantic version", value: catalog.version },
              { label: "Concepts", value: entries.length },
              { label: "Relationships", value: catalog.relationships.length },
            ]}
          />
          <Box margin={{ top: "l" }}>
            <div className="ontology-graph">
              <ReactFlow
                nodes={nodes}
                edges={edges}
                fitView
                minZoom={0.2}
                onNodeClick={(_, node) => setSelected(node.id)}
                nodesDraggable={false}
                aria-label="Ontology entity relationships"
              >
                <Background />
                <Controls showInteractive={false} />
              </ReactFlow>
            </div>
          </Box>
        </Container>
        <Container header={<Header variant="h2">{entity.label}</Header>}>
          <SpaceBetween size="l">
            <FormField label="Concept">
              <Select
                selectedOption={{ label: entity.label, value: selected }}
                options={entries.map(([value, e]) => ({
                  value,
                  label: e.label,
                }))}
                onChange={({ detail }) =>
                  setSelected(detail.selectedOption.value!)
                }
                filteringType="auto"
              />
            </FormField>
            <TextContent>
              <p>{entity.definition}</p>
            </TextContent>
            <KeyValuePairs
              columns={3}
              items={[
                {
                  label: "SQL table",
                  value: <Box variant="code">data.{selected}</Box>,
                },
                { label: "Ontology class", value: entity.class },
                {
                  label: "Aliases",
                  value: entity.aliases?.join(", ") || "Source concept",
                },
              ]}
            />
            <Tabs
              tabs={[
                {
                  id: "relationships",
                  label: "Relationships",
                  content: (
                    <Table
                      variant="embedded"
                      ariaLabels={{ tableLabel: "Concept relationships" }}
                      wrapLines
                      items={catalog.relationships.filter(
                        (r) => r.source === selected || r.target === selected,
                      )}
                      columnDefinitions={[
                        {
                          id: "relation",
                          header: "Relationship",
                          cell: (r) => r.name,
                        },
                        {
                          id: "join",
                          header: "SQL mapping",
                          cell: (r) => (
                            <Box variant="code">
                              {r.source}.{r.column} = {r.target}.
                              {r.target_column}
                            </Box>
                          ),
                        },
                        {
                          id: "meaning",
                          header: "Definition",
                          cell: (r) => r.definition,
                        },
                        {
                          id: "validation",
                          header: "Validation",
                          cell: (r) => (
                            <StatusIndicator
                              type={
                                r.status === "validated" ? "success" : "warning"
                              }
                            >
                              {r.status === "validated"
                                ? "Validated"
                                : `${r.unresolved_count} unresolved`}
                            </StatusIndicator>
                          ),
                        },
                      ]}
                      empty={
                        <Box padding="l" textAlign="center">
                          No direct source relationship asserted.
                        </Box>
                      }
                    />
                  ),
                },
                {
                  id: "fields",
                  label: "Field definitions",
                  content: <FieldDefinitions fields={entity.fields} />,
                },
              ]}
            />
          </SpaceBetween>
        </Container>
        <Container header={<Header variant="h2">Interpretation rules</Header>}>
          <TextContent>
            <ul>
              {catalog.rules.map((rule) => (
                <li key={rule}>{rule}</li>
              ))}
            </ul>
          </TextContent>
        </Container>
      </SpaceBetween>
    </ContentLayout>
  );
}
