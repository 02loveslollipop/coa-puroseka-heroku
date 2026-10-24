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
import { Catalog, apiUrl } from "./api";
import { FieldDefinitions, LoadingState } from "./ui";
import { OntologyGraph } from "./ontology-graph";

export function Ontology({ catalog }: { catalog: Catalog | null }) {
  const [selected, setSelected] = useState("events");
  if (!catalog) return <LoadingState label="Loading ontology" />;
  const entity = catalog.tables[selected];
  const entries = Object.entries(catalog.tables);
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
              description="Drag concepts to rearrange the diagram; select one to inspect its definition and relationships."
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
            <OntologyGraph catalog={catalog} selected={selected} onSelect={setSelected} />
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
