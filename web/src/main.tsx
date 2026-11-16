import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import tokens from "../../design/tokens.json";
import { App } from "./App";
import { tokensCss, type TokenFile } from "./lib/tokens";
import "./styles/app.css";

const style = document.createElement("style");
style.dataset.tokens = "design/tokens.json";
style.textContent = tokensCss(tokens as unknown as TokenFile);
document.head.prepend(style);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
