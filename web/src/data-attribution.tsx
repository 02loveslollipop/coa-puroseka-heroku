import { Box, Link } from "@cloudscape-design/components";

export function DataAttribution() {
  return (
    <section aria-label="Data source">
      <Box variant="h2" fontSize="body-s">Data source</Box>
      <Box color="text-body-secondary" fontSize="body-s">
        Data used in this application is obtained from the{" "}
        <Link external href="https://sekai.best/" fontSize="body-s">
          sekai.best datasets
        </Link>.
      </Box>
    </section>
  );
}
