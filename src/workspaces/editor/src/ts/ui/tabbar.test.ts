import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TabInfo } from "../types";
import { DOM_IDS } from "./constants";
import { type TabBarCallbacks, TabBarView } from "./tabbar";

describe("TabBarView", () => {
  let tabBarContainer: HTMLElement;
  let callbacks: TabBarCallbacks;
  let tabBar: TabBarView;

  beforeEach(() => {
    document.body.innerHTML = "";

    tabBarContainer = document.createElement("div");
    tabBarContainer.id = DOM_IDS.TAB_BAR;
    document.body.appendChild(tabBarContainer);

    callbacks = {
      onSelectTab: vi.fn(),
      onCloseTab: vi.fn(),
      onSetMainTab: vi.fn(),
    };

    tabBar = new TabBarView(callbacks);
  });

  afterEach(() => {
    tabBar.dispose();
    document.body.innerHTML = "";
  });

  it("renders tabs with correct active, main, dirty, title, and tooltip states", () => {
    const tabs: TabInfo[] = [
      {
        uri: "file:///workspace/main.py",
        title: "main.py",
        isMain: true,
        isDirty: true,
        language: "python",
      },
      {
        uri: "file:///workspace/helper.py",
        title: "helper.py",
        isMain: false,
        isDirty: false,
        language: "python",
      },
      {
        uri: "file:///workspace/pkg/__init__.py",
        title: "pkg/__init__.py",
        isMain: false,
        isDirty: true,
        language: "python",
      },
    ];

    tabBar.render(tabs, "file:///workspace/main.py");

    const tabElements = tabBarContainer.children;
    expect(tabElements).toHaveLength(3);

    // Tab 1: active, main, dirty
    const tab1 = tabElements[0] as HTMLElement;
    expect(tab1.classList.contains("tab")).toBe(true);
    expect(tab1.classList.contains("active")).toBe(true);
    expect(tab1.classList.contains("main")).toBe(true);
    expect(tab1.classList.contains("dirty")).toBe(true);

    const mainBadge1 = tab1.querySelector(".tab-main-badge");
    expect(mainBadge1).not.toBeNull();
    expect(mainBadge1?.textContent).toBe("MAIN");

    const title1 = tab1.querySelector<HTMLElement>(".tab-title");
    expect(title1?.textContent).toBe("main.py");
    expect(title1?.title).toBe("file:///workspace/main.py");

    const dirtyDot1 = tab1.querySelector(".tab-dirty-dot");
    expect(dirtyDot1).not.toBeNull();

    const closeBtn1 = tab1.querySelector<HTMLElement>(".tab-close");
    expect(closeBtn1?.textContent).toBe("\u00D7");
    expect(closeBtn1?.title).toBe("Close tab");

    // Tab 2: inactive, not main, not dirty
    const tab2 = tabElements[1] as HTMLElement;
    expect(tab2.classList.contains("tab")).toBe(true);
    expect(tab2.classList.contains("active")).toBe(false);
    expect(tab2.classList.contains("main")).toBe(false);
    expect(tab2.classList.contains("dirty")).toBe(false);

    expect(tab2.querySelector(".tab-main-badge")).toBeNull();
    expect(tab2.querySelector(".tab-dirty-dot")).toBeNull();

    const title2 = tab2.querySelector<HTMLElement>(".tab-title");
    expect(title2?.textContent).toBe("helper.py");
    expect(title2?.title).toBe("file:///workspace/helper.py");

    // Tab 3: inactive, not main, dirty
    const tab3 = tabElements[2] as HTMLElement;
    expect(tab3.classList.contains("tab")).toBe(true);
    expect(tab3.classList.contains("active")).toBe(false);
    expect(tab3.classList.contains("main")).toBe(false);
    expect(tab3.classList.contains("dirty")).toBe(true);
    expect(tab3.querySelector(".tab-dirty-dot")).not.toBeNull();
    expect(tab3.querySelector(".tab-title")?.textContent).toBe("pkg/__init__.py");
  });

  it("clicking tab calls onSelectTab(uri)", () => {
    const tabs: TabInfo[] = [
      {
        uri: "file:///workspace/script1.py",
        title: "script1.py",
        isMain: true,
        isDirty: false,
        language: "python",
      },
      {
        uri: "file:///workspace/script2.py",
        title: "script2.py",
        isMain: false,
        isDirty: false,
        language: "python",
      },
    ];

    tabBar.render(tabs, "file:///workspace/script1.py");

    const tab2El = tabBarContainer.children[1] as HTMLElement;
    tab2El.click();

    expect(callbacks.onSelectTab).toHaveBeenCalledWith("file:///workspace/script2.py");
    expect(callbacks.onCloseTab).not.toHaveBeenCalled();
  });

  it("clicking close button calls onCloseTab(uri) and stops event propagation", () => {
    const tabs: TabInfo[] = [
      {
        uri: "file:///workspace/script1.py",
        title: "script1.py",
        isMain: true,
        isDirty: false,
        language: "python",
      },
    ];

    tabBar.render(tabs, "file:///workspace/script1.py");

    const tab1El = tabBarContainer.children[0] as HTMLElement;
    const closeBtn = tab1El.querySelector<HTMLElement>(".tab-close")!;

    closeBtn.click();

    expect(callbacks.onCloseTab).toHaveBeenCalledWith("file:///workspace/script1.py");
    // onSelectTab should NOT be called due to stopPropagation
    expect(callbacks.onSelectTab).not.toHaveBeenCalled();
  });

  describe("context menu", () => {
    const tabs: TabInfo[] = [
      {
        uri: "file:///workspace/main.py",
        title: "main.py",
        isMain: true,
        isDirty: false,
        language: "python",
      },
      {
        uri: "file:///workspace/secondary.py",
        title: "secondary.py",
        isMain: false,
        isDirty: false,
        language: "python",
      },
    ];

    it("right click on non-main tab renders #tab-context-menu at coordinates", () => {
      tabBar.render(tabs, "file:///workspace/main.py");

      const nonMainTabEl = tabBarContainer.children[1] as HTMLElement;
      const contextMenuEvent = new MouseEvent("contextmenu", {
        bubbles: true,
        cancelable: true,
        clientX: 150,
        clientY: 220,
      });

      nonMainTabEl.dispatchEvent(contextMenuEvent);

      expect(contextMenuEvent.defaultPrevented).toBe(true);

      const menu = document.getElementById(DOM_IDS.TAB_CONTEXT_MENU);
      expect(menu).not.toBeNull();
      expect(menu?.className).toBe("tab-context-menu");
      expect(menu?.style.left).toBe("150px");
      expect(menu?.style.top).toBe("220px");

      const item = menu?.querySelector(".tab-context-menu-item");
      expect(item?.textContent).toBe("⚡ Set as Main Script");
    });

    it("right click on main tab does NOT show context menu", () => {
      tabBar.render(tabs, "file:///workspace/main.py");

      const mainTabEl = tabBarContainer.children[0] as HTMLElement;
      const contextMenuEvent = new MouseEvent("contextmenu", {
        bubbles: true,
        cancelable: true,
        clientX: 50,
        clientY: 50,
      });

      mainTabEl.dispatchEvent(contextMenuEvent);

      expect(contextMenuEvent.defaultPrevented).toBe(true);
      expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).toBeNull();
    });

    it("clicking '⚡ Set as Main Script' calls onSetMainTab(uri) and destroys context menu", () => {
      tabBar.render(tabs, "file:///workspace/main.py");

      const nonMainTabEl = tabBarContainer.children[1] as HTMLElement;
      nonMainTabEl.dispatchEvent(
        new MouseEvent("contextmenu", {
          bubbles: true,
          cancelable: true,
          clientX: 100,
          clientY: 100,
        }),
      );

      const menu = document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)!;
      const item = menu.querySelector<HTMLElement>(".tab-context-menu-item")!;

      item.click();

      expect(callbacks.onSetMainTab).toHaveBeenCalledWith("file:///workspace/secondary.py");
      expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).toBeNull();
    });

    it("pressing Escape closes context menu and disposes window listeners", () => {
      tabBar.render(tabs, "file:///workspace/main.py");

      const nonMainTabEl = tabBarContainer.children[1] as HTMLElement;
      nonMainTabEl.dispatchEvent(
        new MouseEvent("contextmenu", {
          bubbles: true,
          cancelable: true,
          clientX: 100,
          clientY: 100,
        }),
      );

      expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).not.toBeNull();

      // Dispatch Escape keydown on window
      window.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "Escape",
          code: "Escape",
        }),
      );

      expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).toBeNull();
    });

    it("clicking outside closes context menu", () => {
      tabBar.render(tabs, "file:///workspace/main.py");

      const nonMainTabEl = tabBarContainer.children[1] as HTMLElement;
      nonMainTabEl.dispatchEvent(
        new MouseEvent("contextmenu", {
          bubbles: true,
          cancelable: true,
          clientX: 100,
          clientY: 100,
        }),
      );

      expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).not.toBeNull();

      // Dispatch window click
      window.dispatchEvent(new MouseEvent("click", { bubbles: true }));

      expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).toBeNull();
    });

    it("re-rendering tabs closes any open context menu", () => {
      tabBar.render(tabs, "file:///workspace/main.py");

      const nonMainTabEl = tabBarContainer.children[1] as HTMLElement;
      nonMainTabEl.dispatchEvent(
        new MouseEvent("contextmenu", {
          bubbles: true,
          cancelable: true,
          clientX: 100,
          clientY: 100,
        }),
      );

      expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).not.toBeNull();

      tabBar.render(tabs, "file:///workspace/main.py");

      expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).toBeNull();
    });
  });

  it("tabBar.dispose() cleans up any active context menu and resources", () => {
    const tabs: TabInfo[] = [
      {
        uri: "file:///workspace/secondary.py",
        title: "secondary.py",
        isMain: false,
        isDirty: false,
        language: "python",
      },
    ];

    tabBar.render(tabs, "file:///workspace/secondary.py");

    const tabEl = tabBarContainer.children[0] as HTMLElement;
    tabEl.dispatchEvent(
      new MouseEvent("contextmenu", {
        bubbles: true,
        cancelable: true,
        clientX: 100,
        clientY: 100,
      }),
    );

    expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).not.toBeNull();

    tabBar.dispose();

    expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).toBeNull();
  });
});
