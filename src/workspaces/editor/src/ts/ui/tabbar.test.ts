import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';

import type { TabInfo } from '../types';
import { DOM_IDS } from './constants';
import { type TabBarCallbacks, TabBarView } from './tabbar';
import { stubPopoverApi } from './test-helpers';

describe('TabBarView', () => {
  let tabBarContainer: HTMLElement;
  let callbacks: Required<TabBarCallbacks>;
  let tabBar: TabBarView;
  let restorePopoverApi: () => void;

  beforeEach(() => {
    document.body.innerHTML = '';
    restorePopoverApi = stubPopoverApi();

    tabBarContainer = document.createElement('div');
    tabBarContainer.id = DOM_IDS.TAB_BAR;
    document.body.appendChild(tabBarContainer);

    callbacks = {
      onSelectTab: vi.fn(),
      onCloseTab: vi.fn(),
      onCloseOtherTabs: vi.fn(),
      onCloseSavedTabs: vi.fn(),
      onSetMainTab: vi.fn(),
    };

    tabBar = new TabBarView(callbacks);
  });

  afterEach(() => {
    tabBar.dispose();
    restorePopoverApi();
    document.body.innerHTML = '';
  });

  it('renders tabs with correct active, main, dirty, title, tooltip, and SVG close icon', () => {
    const tabs: TabInfo[] = [
      {
        uri: 'file:///workspace/main.py',
        title: 'main.py',
        isMain: true,
        isDirty: true,
        language: 'python',
      },
      {
        uri: 'file:///workspace/helper.py',
        title: 'helper.py',
        isMain: false,
        isDirty: false,
        language: 'python',
      },
      {
        uri: 'file:///workspace/pkg/__init__.py',
        title: 'pkg/__init__.py',
        isMain: false,
        isDirty: true,
        language: 'python',
      },
    ];

    tabBar.render(tabs, 'file:///workspace/main.py');

    const tabElements = tabBarContainer.children;
    expect(tabElements).toHaveLength(3);

    // Tab 1: active, main, dirty
    const tab1 = tabElements[0] as HTMLElement;
    expect(tab1.classList.contains('tab')).toBe(true);
    expect(tab1.classList.contains('active')).toBe(true);
    expect(tab1.classList.contains('main')).toBe(true);
    expect(tab1.classList.contains('dirty')).toBe(true);

    const mainBadge1 = tab1.querySelector('.tab-main-badge');
    expect(mainBadge1).not.toBeNull();
    expect(mainBadge1?.textContent).toBe('MAIN');

    const title1 = tab1.querySelector<HTMLElement>('.tab-title');
    expect(title1?.textContent).toBe('main.py');
    expect(title1?.title).toBe('file:///workspace/main.py');

    const dirtyDot1 = tab1.querySelector('.tab-dirty-dot');
    expect(dirtyDot1).not.toBeNull();

    const closeBtn1 = tab1.querySelector<HTMLButtonElement>('button.tab-close');
    expect(closeBtn1).not.toBeNull();
    expect(closeBtn1?.title).toBe('Close tab');
    expect(closeBtn1?.getAttribute('aria-label')).toBe('Close tab');
    expect(closeBtn1?.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');

    // Tab 2: inactive, not main, not dirty
    const tab2 = tabElements[1] as HTMLElement;
    expect(tab2.classList.contains('tab')).toBe(true);
    expect(tab2.classList.contains('active')).toBe(false);
    expect(tab2.classList.contains('main')).toBe(false);
    expect(tab2.classList.contains('dirty')).toBe(false);

    expect(tab2.querySelector('.tab-main-badge')).toBeNull();
    expect(tab2.querySelector('.tab-dirty-dot')).toBeNull();

    const title2 = tab2.querySelector<HTMLElement>('.tab-title');
    expect(title2?.textContent).toBe('helper.py');
    expect(title2?.title).toBe('file:///workspace/helper.py');

    // Tab 3: inactive, not main, dirty
    const tab3 = tabElements[2] as HTMLElement;
    expect(tab3.classList.contains('tab')).toBe(true);
    expect(tab3.classList.contains('active')).toBe(false);
    expect(tab3.classList.contains('main')).toBe(false);
    expect(tab3.classList.contains('dirty')).toBe(true);
    expect(tab3.querySelector('.tab-dirty-dot')).not.toBeNull();
    expect(tab3.querySelector('.tab-title')?.textContent).toBe('pkg/__init__.py');
  });

  it('clicking tab calls onSelectTab(uri)', () => {
    const tabs: TabInfo[] = [
      {
        uri: 'file:///workspace/script1.py',
        title: 'script1.py',
        isMain: true,
        isDirty: false,
        language: 'python',
      },
      {
        uri: 'file:///workspace/script2.py',
        title: 'script2.py',
        isMain: false,
        isDirty: false,
        language: 'python',
      },
    ];

    tabBar.render(tabs, 'file:///workspace/script1.py');

    const tab2El = tabBarContainer.children[1] as HTMLElement;
    tab2El.click();

    expect(callbacks.onSelectTab).toHaveBeenCalledWith('file:///workspace/script2.py');
    expect(callbacks.onCloseTab).not.toHaveBeenCalled();
  });

  it('clicking close button calls onCloseTab(uri) and stops event propagation', () => {
    const tabs: TabInfo[] = [
      {
        uri: 'file:///workspace/script1.py',
        title: 'script1.py',
        isMain: true,
        isDirty: false,
        language: 'python',
      },
    ];

    tabBar.render(tabs, 'file:///workspace/script1.py');

    const tab1El = tabBarContainer.children[0] as HTMLElement;
    const closeBtn = tab1El.querySelector<HTMLElement>('.tab-close')!;

    closeBtn.click();

    expect(callbacks.onCloseTab).toHaveBeenCalledWith('file:///workspace/script1.py');
    expect(callbacks.onSelectTab).not.toHaveBeenCalled();
  });

  it('middle-clicking tab prevents default autoscroll on mousedown and closes tab on auxclick', () => {
    const tabs: TabInfo[] = [
      {
        uri: 'file:///workspace/script1.py',
        title: 'script1.py',
        isMain: true,
        isDirty: false,
        language: 'python',
      },
    ];

    tabBar.render(tabs, 'file:///workspace/script1.py');

    const tab1El = tabBarContainer.children[0] as HTMLElement;

    const mouseDownEvent = new MouseEvent('mousedown', {
      button: 1,
      bubbles: true,
      cancelable: true,
    });
    tab1El.dispatchEvent(mouseDownEvent);
    expect(mouseDownEvent.defaultPrevented).toBe(true);

    const leftDownEvent = new MouseEvent('mousedown', {
      button: 0,
      bubbles: true,
      cancelable: true,
    });
    tab1El.dispatchEvent(leftDownEvent);
    expect(leftDownEvent.defaultPrevented).toBe(false);

    const auxClickEvent = new MouseEvent('auxclick', {
      button: 1,
      bubbles: true,
      cancelable: true,
    });
    tab1El.dispatchEvent(auxClickEvent);
    expect(callbacks.onCloseTab).toHaveBeenCalledWith('file:///workspace/script1.py');
  });

  it('translates vertical wheel events on #tab-bar into horizontal scrollLeft only when overflowing and not Ctrl-zooming', () => {
    tabBarContainer.scrollLeft = 10;

    // 1. Not overflowing (scrollWidth <= clientWidth): should not preventDefault or scroll
    Object.defineProperty(tabBarContainer, 'scrollWidth', { value: 200, configurable: true });
    Object.defineProperty(tabBarContainer, 'clientWidth', { value: 200, configurable: true });

    const nonOverflowWheel = new WheelEvent('wheel', {
      deltaY: 45,
      bubbles: true,
      cancelable: true,
    });
    tabBarContainer.dispatchEvent(nonOverflowWheel);
    expect(nonOverflowWheel.defaultPrevented).toBe(false);
    expect(tabBarContainer.scrollLeft).toBe(10);

    // 2. Overflowing (scrollWidth > clientWidth): scrolls horizontally
    Object.defineProperty(tabBarContainer, 'scrollWidth', { value: 500, configurable: true });
    Object.defineProperty(tabBarContainer, 'clientWidth', { value: 200, configurable: true });

    const wheelEvent = new WheelEvent('wheel', {
      deltaY: 45,
      bubbles: true,
      cancelable: true,
    });
    tabBarContainer.dispatchEvent(wheelEvent);
    expect(wheelEvent.defaultPrevented).toBe(true);
    expect(tabBarContainer.scrollLeft).toBe(55);

    // 3. Ctrl+wheel (zoom): should not preventDefault or scroll
    const ctrlWheel = new WheelEvent('wheel', {
      deltaY: 45,
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(ctrlWheel, 'ctrlKey', { value: true });
    tabBarContainer.dispatchEvent(ctrlWheel);
    expect(ctrlWheel.defaultPrevented).toBe(false);
    expect(tabBarContainer.scrollLeft).toBe(55);
  });

  describe('tab semantics', () => {
    const tabs: TabInfo[] = [
      {
        uri: 'file:///workspace/main.py',
        title: 'main.py',
        isMain: true,
        isDirty: true,
        language: 'python',
      },
      {
        uri: 'file:///workspace/helper.py',
        title: 'helper.py',
        isMain: false,
        isDirty: false,
        language: 'python',
      },
      {
        uri: 'file:///workspace/other.py',
        title: 'other.py',
        isMain: false,
        isDirty: false,
        language: 'python',
      },
    ];

    const tabElements = () =>
      Array.from(tabBarContainer.querySelectorAll<HTMLElement>('[role="tab"]'));

    it('exposes a named tablist with the selected tab and the editor as its panel', () => {
      const panel = document.createElement('div');
      panel.id = DOM_IDS.EDITOR;
      document.body.appendChild(panel);

      tabBar.render(tabs, 'file:///workspace/helper.py');

      expect(tabBarContainer.getAttribute('role')).toBe('tablist');
      expect(tabBarContainer.getAttribute('aria-label')).toBe('Open scripts');
      expect(tabElements().map((tab) => tab.getAttribute('aria-selected'))).toEqual([
        'false',
        'true',
        'false',
      ]);
      expect(tabElements()[0]?.getAttribute('aria-label')).toBe(
        'main.py, main script, unsaved changes',
      );
      expect(tabElements()[1]?.getAttribute('aria-label')).toBe('helper.py');
      expect(panel.getAttribute('role')).toBe('tabpanel');
      expect(panel.getAttribute('aria-labelledby')).toBe(tabElements()[1]?.id);
    });
  });

  describe('context menu', () => {
    const tabs: TabInfo[] = [
      {
        uri: 'file:///workspace/main.py',
        title: 'main.py',
        isMain: true,
        isDirty: true,
        language: 'python',
      },
      {
        uri: 'file:///workspace/secondary.py',
        title: 'secondary.py',
        isMain: false,
        isDirty: false,
        language: 'python',
      },
    ];

    const openMenu = (tabEl: HTMLElement, clientX = 100, clientY = 100) => {
      const event = new MouseEvent('contextmenu', {
        bubbles: true,
        cancelable: true,
        clientX,
        clientY,
      });
      tabEl.dispatchEvent(event);
      return event;
    };

    const menuItems = () =>
      document
        .getElementById(DOM_IDS.TAB_CONTEXT_MENU)!
        .querySelectorAll<HTMLElement>('.context-menu-item');

    it('right click on non-main tab opens a popover menu at the pointer with all items', () => {
      tabBar.render(tabs, 'file:///workspace/main.py');

      const event = openMenu(tabBarContainer.children[1] as HTMLElement, 150, 220);

      expect(event.defaultPrevented).toBe(true);

      const menu = document.getElementById(DOM_IDS.TAB_CONTEXT_MENU);
      expect(menu).not.toBeNull();
      expect(menu?.getAttribute('popover')).toBe('auto');
      expect(menu?.getAttribute('role')).toBe('menu');
      expect(menu?.hasAttribute('data-popover-open')).toBe(true);

      const anchor = document.querySelector<HTMLElement>('.context-menu-anchor');
      expect(anchor?.style.left).toBe('150px');
      expect(anchor?.style.top).toBe('220px');

      const items = menuItems();
      expect(Array.from(items, (item) => item.textContent.trim())).toEqual([
        '⚡ Set as Main Script',
        'Close',
        'Close Others',
        'Close Saved',
      ]);
      expect(Array.from(items, (item) => item.getAttribute('aria-disabled'))).toEqual([
        null,
        null,
        null,
        null,
      ]);
    });

    it('right click on main tab opens menu with Set as Main Script disabled', () => {
      tabBar.render(tabs, 'file:///workspace/main.py');

      openMenu(tabBarContainer.children[0] as HTMLElement, 50, 50);

      const setMainItem = menuItems()[0]!;
      expect(setMainItem.getAttribute('aria-disabled')).toBe('true');

      // Clicking disabled item does not invoke callback or close menu
      setMainItem.click();
      expect(callbacks.onSetMainTab).not.toHaveBeenCalled();
      expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).not.toBeNull();
    });

    it("clicking '⚡ Set as Main Script', 'Close', 'Close Others', and 'Close Saved' invokes callbacks and closes menu", () => {
      tabBar.render(tabs, 'file:///workspace/main.py');
      const nonMainTabEl = tabBarContainer.children[1] as HTMLElement;

      // 1. Set as Main Script
      openMenu(nonMainTabEl);
      menuItems()[0]!.click();
      expect(callbacks.onSetMainTab).toHaveBeenCalledWith('file:///workspace/secondary.py');
      expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).toBeNull();

      // 2. Close
      openMenu(nonMainTabEl);
      menuItems()[1]!.click();
      expect(callbacks.onCloseTab).toHaveBeenCalledWith('file:///workspace/secondary.py');
      expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).toBeNull();

      // 3. Close Others
      openMenu(nonMainTabEl);
      menuItems()[2]!.click();
      expect(callbacks.onCloseOtherTabs).toHaveBeenCalledWith('file:///workspace/secondary.py');
      expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).toBeNull();

      // 4. Close Saved
      openMenu(nonMainTabEl);
      menuItems()[3]!.click();
      expect(callbacks.onCloseSavedTabs).toHaveBeenCalledTimes(1);
      expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).toBeNull();
    });

    it('disables Close Others when only one tab exists and Close Saved when all tabs are dirty', () => {
      const singleDirtyTab: TabInfo[] = [
        {
          uri: 'file:///workspace/only.py',
          title: 'only.py',
          isMain: true,
          isDirty: true,
          language: 'python',
        },
      ];

      tabBar.render(singleDirtyTab, 'file:///workspace/only.py');
      openMenu(tabBarContainer.children[0] as HTMLElement, 40, 40);

      const items = menuItems();
      expect(items[2]?.getAttribute('aria-disabled')).toBe('true'); // Close Others
      expect(items[3]?.getAttribute('aria-disabled')).toBe('true'); // Close Saved
    });

    it('removes the menu when the browser dismisses the popover (Escape or outside click)', () => {
      tabBar.render(tabs, 'file:///workspace/main.py');
      openMenu(tabBarContainer.children[1] as HTMLElement);

      document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)!.hidePopover();

      expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).toBeNull();
      expect(document.querySelector('.context-menu-anchor')).toBeNull();
    });

    it('keeps a reopened menu when the replaced menu reports its close late', () => {
      tabBar.render(tabs, 'file:///workspace/main.py');
      openMenu(tabBarContainer.children[1] as HTMLElement);
      const staleMenu = document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)!;

      openMenu(tabBarContainer.children[0] as HTMLElement);
      // Browsers fire `toggle` from a queued task, so the old menu's close can arrive after this.
      staleMenu.dispatchEvent(Object.assign(new Event('toggle'), { newState: 'closed' }));

      const menu = document.getElementById(DOM_IDS.TAB_CONTEXT_MENU);
      expect(menu).not.toBeNull();
      expect(menu).not.toBe(staleMenu);
      expect(menu?.hasAttribute('data-popover-open')).toBe(true);
    });

    it('re-rendering tabs closes any open context menu', () => {
      tabBar.render(tabs, 'file:///workspace/main.py');
      openMenu(tabBarContainer.children[1] as HTMLElement);
      expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).not.toBeNull();

      tabBar.render(tabs, 'file:///workspace/main.py');

      expect(document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)).toBeNull();
    });
  });

  it('tabBar.dispose() cleans up any active context menu and resources', () => {
    const tabs: TabInfo[] = [
      {
        uri: 'file:///workspace/secondary.py',
        title: 'secondary.py',
        isMain: false,
        isDirty: false,
        language: 'python',
      },
    ];

    tabBar.render(tabs, 'file:///workspace/secondary.py');

    const tabEl = tabBarContainer.children[0] as HTMLElement;
    tabEl.dispatchEvent(
      new MouseEvent('contextmenu', {
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
