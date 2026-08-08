/**
 * PinScopeMenu テスト (Phase G-47).
 *
 * カバーするシナリオ:
 *   1. trigger button (children) が描画される
 *   2. trigger click で popover が開き 2 つの menu item が表示される
 *   3. 「この曜日のみ」 click で onSelect(pfvId, !isPinned, 'day', patientId)
 *   4. 「この患者の全曜日」 click で onSelect(pfvId, !isPinned, 'all-days', patientId)
 *   5. isPinned=true → menu の表記が「解除」 系に切り替わる
 *   6. isPinned=false → menu の表記が「ロック」 系に切り替わる
 *   7. disabled=true → trigger click で popover が開かない / onSelect が呼ばれない
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { PinScopeMenu } from '../PinScopeMenu';

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

interface RenderOpts {
  pfvId?: string;
  patientId?: string;
  isPinned?: boolean;
  disabled?: boolean;
  onSelect?: ReturnType<typeof vi.fn>;
}

function renderMenu(opts: RenderOpts = {}) {
  const {
    pfvId = 'pfv-1',
    patientId = 'p-1',
    isPinned = false,
    disabled = false,
    onSelect = vi.fn(),
  } = opts;
  const utils = render(
    <PinScopeMenu
      pfvId={pfvId}
      patientId={patientId}
      isPinned={isPinned}
      onSelect={onSelect}
      disabled={disabled}
      testIdSuffix="t1"
    >
      {({ isOpen }) => (
        <button
          type="button"
          data-testid="trigger"
          data-open={isOpen ? 'true' : 'false'}
          disabled={disabled}
        >
          🔒
        </button>
      )}
    </PinScopeMenu>,
  );
  return { ...utils, onSelect };
}

// ─────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────

describe('PinScopeMenu (Phase G-47)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('1. trigger button (children) が描画される', () => {
    renderMenu();
    expect(screen.getByTestId('trigger')).toBeInTheDocument();
    // 初期は popover 閉じている
    expect(screen.getByTestId('trigger')).toHaveAttribute('data-open', 'false');
  });

  it('2. trigger click で popover が開き 2 つの menu item が表示される', () => {
    renderMenu({ isPinned: false });
    fireEvent.click(screen.getByTestId('trigger'));
    // popover content 内の menu item を検索 (testIdSuffix='t1')
    expect(screen.getByTestId('pin-scope-menu-day-t1')).toBeInTheDocument();
    expect(screen.getByTestId('pin-scope-menu-all-days-t1')).toBeInTheDocument();
  });

  it('3. 「この曜日のみ」 click で onSelect(pfvId, !isPinned, "day", patientId)', () => {
    const onSelect = vi.fn();
    renderMenu({
      pfvId: 'pfv-A',
      patientId: 'p-A',
      isPinned: false,
      onSelect,
    });
    fireEvent.click(screen.getByTestId('trigger'));
    fireEvent.click(screen.getByTestId('pin-scope-menu-day-t1'));
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect).toHaveBeenCalledWith('pfv-A', true, 'day', 'p-A');
  });

  it('4. 「この患者の全曜日」 click で onSelect(pfvId, !isPinned, "all-days", patientId)', () => {
    const onSelect = vi.fn();
    renderMenu({
      pfvId: 'pfv-B',
      patientId: 'p-B',
      isPinned: false,
      onSelect,
    });
    fireEvent.click(screen.getByTestId('trigger'));
    fireEvent.click(screen.getByTestId('pin-scope-menu-all-days-t1'));
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect).toHaveBeenCalledWith('pfv-B', true, 'all-days', 'p-B');
  });

  it('5. isPinned=true → menu の表記が「完全固定を解除」 系に切り替わる', () => {
    renderMenu({ isPinned: true });
    fireEvent.click(screen.getByTestId('trigger'));
    expect(screen.getByTestId('pin-scope-menu-day-t1')).toHaveTextContent(
      'この曜日のみ 完全固定を解除',
    );
    expect(screen.getByTestId('pin-scope-menu-all-days-t1')).toHaveTextContent(
      'この患者の全曜日 完全固定を解除',
    );
  });

  it('6. isPinned=false → menu の表記が「完全固定」 系に切り替わる', () => {
    renderMenu({ isPinned: false });
    fireEvent.click(screen.getByTestId('trigger'));
    expect(screen.getByTestId('pin-scope-menu-day-t1')).toHaveTextContent('この曜日のみ 完全固定');
    expect(screen.getByTestId('pin-scope-menu-all-days-t1')).toHaveTextContent(
      'この患者の全曜日 完全固定',
    );
  });

  it('7. disabled=true → trigger click しても popover は開かず onSelect も呼ばれない', () => {
    const onSelect = vi.fn();
    renderMenu({ disabled: true, onSelect });
    // disabled のときは Popover 自体を render しない (children のみ通す) — menu item は描画されない.
    expect(screen.queryByTestId('pin-scope-menu-day-t1')).not.toBeInTheDocument();
    expect(screen.queryByTestId('pin-scope-menu-all-days-t1')).not.toBeInTheDocument();
    // trigger は描画される (button disabled)
    expect(screen.getByTestId('trigger')).toBeDisabled();
    fireEvent.click(screen.getByTestId('trigger'));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('8. isPinned=true の状態で 「全曜日」 選択 → nextPinned=false', () => {
    const onSelect = vi.fn();
    renderMenu({
      pfvId: 'pfv-C',
      patientId: 'p-C',
      isPinned: true,
      onSelect,
    });
    fireEvent.click(screen.getByTestId('trigger'));
    fireEvent.click(screen.getByTestId('pin-scope-menu-all-days-t1'));
    expect(onSelect).toHaveBeenCalledWith('pfv-C', false, 'all-days', 'p-C');
  });
});
