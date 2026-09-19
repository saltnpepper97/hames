import { render } from "solid-js/web";
import "@fontsource-variable/onest";
import "@fontsource-variable/geist-mono";
import { App } from "./App";
import "./styles.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("Hames Web root element is missing");
}

render(() => <App />, root);
