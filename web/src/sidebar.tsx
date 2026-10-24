import { ReactNode, useLayoutEffect, useRef } from "react";
import { Box, Divider } from "@cloudscape-design/components";
import { DataAttribution } from "./data-attribution";

export function Sidebar({ children }: { children: ReactNode }) {
  const layout = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = layout.current;
    const navigation = element?.closest("nav");
    if (!element || !navigation) return;
    const fit = () => {
      if (navigation.clientHeight > 0) {
        element.style.blockSize = `${navigation.clientHeight}px`;
      }
    };
    // Cloudscape's navigation slot can grow with its content. Size our layout
    // to the actual drawer so long lists scroll above the fixed attribution.
    const observer = new ResizeObserver(fit);
    observer.observe(navigation);
    fit();
    return () => observer.disconnect();
  }, []);
  return (
    <div className="sidebar-layout" ref={layout}>
      <div className="sidebar-navigation">{children}</div>
      <div className="sidebar-attribution">
        <Box padding={{ horizontal: "l", bottom: "l" }}>
          <Divider />
          <Box padding={{ top: "l" }}>
            <DataAttribution />
          </Box>
        </Box>
      </div>
    </div>
  );
}
