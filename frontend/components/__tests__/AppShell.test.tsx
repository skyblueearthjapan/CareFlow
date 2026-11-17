/**
 * AppShell — 狭い窓の初回表示 (narrow-window-layout-design-2026-11-18.md B・C)。
 *
 *   B  縦が 900 未満の初回はスケジュールの上部をコンパクト表示にする (1 度だけ)
 *   C  横が 1400 未満の初回は保留プールを畳む (1 度だけ)
 *   どちらも利用者が切り替えた後は、次に開いたときもそれを守る。広い窓では何もしない。
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';

vi.mock('@/components/Sidebar', () => ({ Sidebar: () => <nav /> }));
vi.mock('@/components/Header', () => ({ Header: () => <header /> }));
vi.mock('@/components/agent/AgentWidget', () => ({ AgentWidget: () => null }));

import { AppShell } from '@/components/AppShell';
import { useUIStore } from '@/lib/stores/ui';

const original = { w: window.innerWidth, h: window.innerHeight };

function setViewport(w: number, h: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: w });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: h });
}

function mount() {
  return render(
    <AppShell>
      <div />
    </AppShell>,
  );
}

describe('AppShell — 狭い窓の初回表示', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useUIStore.setState({
      sidebarCollapsed: false,
      sidebarAutoCollapsedApplied: false,
      scheduleHeaderCollapsed: false,
      scheduleHeaderAutoCollapsedApplied: false,
      schedulePoolCollapsed: false,
      schedulePoolAutoCollapsedApplied: false,
    });
  });

  afterEach(() => setViewport(original.w, original.h));

  it('Mac の Safari のいつもの窓 (1324×840) では上部をコンパクトに・保留プールを畳む', () => {
    setViewport(1324, 840);
    mount();
    expect(useUIStore.getState().scheduleHeaderCollapsed).toBe(true);
    expect(useUIStore.getState().schedulePoolCollapsed).toBe(true);
  });

  it('広い窓 (2048×1250) では何も変えない', () => {
    setViewport(2048, 1250);
    mount();
    expect(useUIStore.getState().scheduleHeaderCollapsed).toBe(false);
    expect(useUIStore.getState().schedulePoolCollapsed).toBe(false);
  });

  it('利用者が開き直した後は、狭い窓で開き直しても畳まない', () => {
    setViewport(1324, 840);
    const first = mount();
    useUIStore.getState().setScheduleHeaderCollapsed(false);
    useUIStore.getState().setSchedulePoolCollapsed(false);
    first.unmount();
    mount();
    expect(useUIStore.getState().scheduleHeaderCollapsed).toBe(false);
    expect(useUIStore.getState().schedulePoolCollapsed).toBe(false);
  });
});
