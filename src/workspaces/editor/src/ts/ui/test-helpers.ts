/** Minimal Popover API stub for happy-dom; returns a teardown function. */
export function stubPopoverApi(): () => void {
  const proto = HTMLElement.prototype as unknown as Record<string, unknown>;

  Object.defineProperties(proto, {
    showPopover: {
      configurable: true,
      writable: true,
      value(this: HTMLElement): void {
        this.setAttribute('data-popover-open', '');
      },
    },
    hidePopover: {
      configurable: true,
      writable: true,
      value(this: HTMLElement): void {
        this.removeAttribute('data-popover-open');
        this.dispatchEvent(Object.assign(new Event('toggle'), { newState: 'closed' }));
      },
    },
  });

  return () => {
    delete proto.showPopover;
    delete proto.hidePopover;
  };
}
