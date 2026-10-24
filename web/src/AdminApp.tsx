// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
// Modified: fixed-domain navigation, notifications and native Cloudscape layouts.
import { useEffect, useState } from "react";
import { Route, Routes, useLocation, useNavigate } from "react-router-dom";
import {
  AppLayout,
  SideNavigation,
  TopNavigation,
  BreadcrumbGroup,
  Flashbar,
} from "@cloudscape-design/components";
import {
  applyMode,
  applyDensity,
  Mode,
  Density,
} from "@cloudscape-design/global-styles";
import { Sources, Ontology, Playground } from "./pages";
import { api, Catalog } from "./api";
import { TokenSettings } from "./token-settings";
import { useAuth } from "./auth";
import { repositoryLink } from "./project";
import { Sidebar } from "./sidebar";

export default function App() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [dark, setDark] = useState(
    localStorage.getItem("scl.darkMode") === "1",
  );
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    applyMode(dark ? Mode.Dark : Mode.Light);
    applyDensity(Density.Comfortable);
    localStorage.setItem("scl.darkMode", dark ? "1" : "0");
  }, [dark]);
  useEffect(() => {
    api<Catalog>("/catalog")
      .then(setCatalog)
      .catch((e) => setError(e.message));
  }, []);
  const labels: Record<string, string> = {
    "/admin/sources": "Data sources",
    "/admin/ontology": "Ontology explorer",
    "/admin/playground": "Inference playground",
    "/admin/access": "Token limits",
  };
  return (
    <>
      <div id="top-navigation">
        <TopNavigation
          identity={{ href: "/admin/sources", title: "Puroseka · COA administration" }}
          utilities={[
            { type: "button", text: "Open chat", href: "/" },
            {
              type: "button",
              text: dark ? "Light mode" : "Dark mode",
              onClick: () => setDark(!dark),
            },
            { type: "menu-dropdown", text: user.username, items: [{ id: "logout", text: "Sign out" }], onItemClick: () => void signOut() },
            repositoryLink,
          ]}
        />
      </div>
      <AppLayout
        headerSelector="#top-navigation"
        toolsHide
        maxContentWidth={1700}
        ariaLabels={{
          navigation: "Service navigation",
          navigationToggle: "Toggle navigation",
          navigationClose: "Close navigation",
        }}
        notifications={
          error ? (
            <Flashbar
              items={[
                {
                  type: "error",
                  header: "Unable to load source catalog",
                  content: error,
                  id: "catalog",
                },
              ]}
            />
          ) : undefined
        }
        navigation={
          <Sidebar>
            <SideNavigation
              activeHref={location.pathname}
              header={{ href: "/admin/sources", text: "COA administration" }}
              onFollow={(event) => {
                if (!event.detail.external) {
                  event.preventDefault();
                  navigate(event.detail.href);
                }
              }}
              items={[
                {
                  type: "section",
                  text: "Scan",
                  items: [{ type: "link", text: "Data sources", href: "/admin/sources" }],
                },
                {
                  type: "section",
                  text: "Model",
                  items: [
                    {
                      type: "link",
                      text: "Ontology explorer",
                      href: "/admin/ontology",
                    },
                  ],
                },
                {
                  type: "section",
                  text: "Serve",
                  items: [
                    {
                      type: "link",
                      text: "Inference playground",
                      href: "/admin/playground",
                    },
                  ],
                },
                { type: "section", text: "Manage", items: [{ type: "link", text: "Token limits", href: "/admin/access" }] },
              ]}
            />
          </Sidebar>
        }
        breadcrumbs={
          <BreadcrumbGroup
            items={[
              { text: "COA administration", href: "/admin/sources" },
              {
                text: labels[location.pathname] || "Data sources",
                href: location.pathname,
              },
            ]}
            onFollow={(event) => {
              event.preventDefault();
              navigate(event.detail.href);
            }}
          />
        }
        content={
          <>
            <Routes>
              <Route path="sources" element={<Sources catalog={catalog} />} />
              <Route
                path="ontology"
                element={<Ontology catalog={catalog} />}
              />
              <Route
                path="playground"
                element={<Playground catalog={catalog} />}
              />
              <Route path="access" element={<TokenSettings />} />
              <Route path="*" element={<Sources catalog={catalog} />} />
            </Routes>
          </>
        }
      />
    </>
  );
}
