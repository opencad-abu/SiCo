/* Progressive enhancement only: content and links remain usable without JS. */
(() => {
  "use strict";
  const themeButton = document.querySelector("#theme-toggle");
  const setTheme = (theme) => {
    document.documentElement.dataset.theme = theme;
    themeButton.textContent = theme === "dark" ? "浅色" : "深色";
    themeButton.setAttribute("aria-label", theme === "dark" ? "切换为浅色主题" : "切换为深色主题");
  };
  try { setTheme(localStorage.getItem("sico-manual-theme") === "dark" ? "dark" : "light"); }
  catch (_) { setTheme("light"); }
  themeButton.addEventListener("click", () => {
    const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    setTheme(theme);
    try { localStorage.setItem("sico-manual-theme", theme); } catch (_) { /* file:// may deny storage */ }
  });
  document.querySelector(".print-button").addEventListener("click", () => window.print());
  const menuButton = document.querySelector("#menu-toggle");
  const sidebar = document.querySelector("#sidebar");
  const closeMenu = () => {
    sidebar.classList.remove("is-open");
    menuButton.setAttribute("aria-expanded", "false");
  };
  menuButton.addEventListener("click", () => {
    menuButton.setAttribute("aria-expanded", String(sidebar.classList.toggle("is-open")));
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && sidebar.classList.contains("is-open")) {
      closeMenu();
      menuButton.focus();
    }
  });
  document.querySelector("main").addEventListener("click", closeMenu);
  document.querySelectorAll(".code-block").forEach((block) => {
    const button = document.createElement("button");
    button.className = "copy";
    button.type = "button";
    button.textContent = "复制";
    button.setAttribute("aria-label", `复制${block.querySelector(".code-label").textContent}`);
    button.addEventListener("click", async () => {
      const code = block.querySelector("code");
      try {
        await navigator.clipboard.writeText(code.textContent);
        button.textContent = "已复制";
      } catch (_) {
        const range = document.createRange();
        range.selectNodeContents(code);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        button.textContent = "已选中，请复制";
      }
      setTimeout(() => { button.textContent = "复制"; }, 2200);
    });
    block.querySelector(".code-label").append(button);
  });
  const search = document.querySelector("#env-search");
  const category = document.querySelector("#env-category");
  if (search) {
    const rows = [...document.querySelectorAll(".env tbody tr")];
    const groups = [...document.querySelectorAll(".env-section")];
    const update = () => {
      const terms = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      let count = 0;
      for (const row of rows) {
        const matches = terms.every((term) => row.textContent.toLowerCase().includes(term));
        const inCategory = !category.value || row.closest(".env-section").id === category.value;
        row.hidden = !(matches && inCategory);
        if (!row.hidden) count += 1;
      }
      groups.forEach((group) => { group.hidden = ![...group.querySelectorAll("tbody tr")].some((row) => !row.hidden); });
      document.querySelector("#env-count").textContent = count ? `显示 ${count} / ${rows.length} 项。` : "没有匹配项，请更换关键词或选择全部分类。";
    };
    search.addEventListener("input", update);
    category.addEventListener("change", update);
    document.querySelector("#env-reset").addEventListener("click", () => {
      search.value = "";
      category.value = "";
      update();
      search.focus();
    });
    const revealAnchor = () => {
      const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
      if (!target || !target.closest(".env-section")) return;
      search.value = "";
      category.value = "";
      update();
      target.scrollIntoView({ block: "start" });
    };
    window.addEventListener("hashchange", revealAnchor);
    update();
    if (location.hash) revealAnchor();
  }
})();
