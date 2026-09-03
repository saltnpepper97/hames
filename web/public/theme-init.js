try {
  const theme = localStorage.getItem("hames.theme") === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute(
    "content",
    theme === "dark" ? "#18181b" : "#ffffff",
  );
} catch {}
