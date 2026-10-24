import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import I18nProvider from "@cloudscape-design/components/i18n";
import messages from "@cloudscape-design/components/i18n/messages/all.en";
import {
  fontFamilyBase,
  fontSizeBodyM,
  lineHeightBodyM,
  colorTextBodyDefault,
  colorBackgroundLayoutMain,
} from "@cloudscape-design/design-tokens";
import "@cloudscape-design/global-styles/index.css";
import App from "./App";
import "./styles.css";

document.body.classList.add("awsui-visual-refresh");
Object.assign(document.body.style, {
  fontFamily: fontFamilyBase,
  fontSize: fontSizeBodyM,
  lineHeight: lineHeightBodyM,
  color: colorTextBodyDefault,
  backgroundColor: colorBackgroundLayoutMain,
});
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <I18nProvider locale="en" messages={[messages]}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </I18nProvider>
  </React.StrictMode>,
);
