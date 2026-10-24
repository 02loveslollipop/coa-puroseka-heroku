# Cloudscape UI integration

The official source was pulled from https://github.com/cloudscape-design/components into `.vendor/cloudscape-components` at commit `43480f95a039d3f48a35d13f6ec44df4e4830e2f`.

Its README recommends consuming the published npm modules. The application uses `@cloudscape-design/components` pinned to `3.0.1404` with the matching official global styles, design tokens, collection hooks, code-view and chat-components packages. `web/package-lock.json` records exact dependency versions. The source checkout is a local reference and is excluded from Docker images and version control.

The table-view and details-with-tabs patterns use `ContentLayout`, `Header`, `Table`, `TextFilter`, `Pagination`, and `useCollection`. Source tables support native sorting, filtering, selection, and pagination. The record preview has server-side pagination.

The inference page uses `PromptInput`, `SupportPromptGroup`, `ChatBubble`, `Avatar`, native status indicators, `TextContent`, `CodeView`, and `CopyToClipboard`. All mode selection, history, comparison, SQL evidence and context behavior is preserved.

The ranking chart uses Cloudscape's supported `LineChart` component with keyboard navigation, UTC axes, a native legend and data popovers. The newer Highcharts-based charts are a separate commercial dependency; this small PoC uses the maintained component available directly in the pulled components repository.

React Flow remains the ontology graph renderer. Its node and edge colors and typography use Cloudscape design tokens. Custom CSS is limited to graph/trace viewport sizing; no Cloudscape component internals are restyled.

Official references:

- https://cloudscape.design/get-started/for-developers/start-developing/
- https://cloudscape.design/patterns/resource-management/view/table-view/
- https://cloudscape.design/get-started/for-developers/global-styles/
- https://cloudscape.design/components/

For application-only deployment through CloudFormation:

```bash
.venv/bin/python scripts/aws.py deploy --app-only
```

This replaces the application image without rerunning the database import or rewriting its model configuration.

The dedicated user chat now opens at `/`, with COA pages under `/admin`. It follows Cloudscape's generative AI chat patterns, including a pinned composer, scrollable message log, support prompts, loading avatars, conversation history, copy actions, and regeneration. See [user chat and access](user-chat.md). Both interfaces require sign-in; token enforcement remains off for the test.

The response-agent selector, A/B toggle, and administrator Review mode toggle are grouped in the chat sidebar. A separate Tool activity section precedes each answer bubble and follows Cloudscape's progressive-steps pattern using native `Steps` and `ExpandableSection` components. Safe execution summaries update during inference; the completed timeline starts collapsed. Review mode adds expandable tool/model input and output JSON in the timeline, and executed SQL plus bounded result rows inside the answer bubble. Both A/B responses have independent timelines and answer bubbles. Mobile navigation exposes the same sidebar controls without reducing the conversation header to a settings form.

## Verification on 2026-10-23

The initial Cloudscape update completed with one healthy ECS task using image tag `67825da9c0ab`. Its endpoint at that verification was http://3.236.31.132:8000. The authenticated chat now uses the stable HTTPS URL in the README.

The frontend type check and production build passed, as did all 29 backend and PostgreSQL integration tests. Browser checks covered native table filtering/pagination, the ranking chart, ontology navigation, and Base/Enriched comparison with SQL code views. No browser console errors were reported during these flows.

The deployed health check confirms `ministral-8b-latest` and semantic version `puroseka-1.1.0`. Its catalog still contains 16 tables and 1,117,619 ranking observations. Event 1 at rank 100 returns all 258 available observations for the chart. Submitting “How many events are in the dataset?” through the deployed prompt returned 180 with matching SQL evidence. Deployed UI screenshots are saved at `output/playwright/cloudscape-hosted-sources.png` and `output/playwright/cloudscape-hosted-playground.png`.
