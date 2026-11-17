/**
 * 畳んだ保留プールへ戻せる (narrow-window-layout-design-2026-11-18.md C・レビューの指摘)。
 *
 * 畳んでいる間は細い帯 (`PoolCollapsedStrip`) が `POOL_DROPPABLE_ID` の drop 先になり、
 * 中の drop 領域は別の id に退いて無効になる。開くと中の drop 領域が `POOL_DROPPABLE_ID`
 * に戻る。dnd-kit の本物の DndContext に登録された drop 先で確かめる。
 */
import * as React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DndContext, useDndContext } from '@dnd-kit/core';

import { POOL_DROPPABLE_ID, PoolCollapsedStrip, PoolGroupedByWeekday } from '../PoolPanel';

/** 登録された drop 先の id → { node の testid, disabled } を書き出す。 */
function DroppableProbe() {
  const { droppableContainers } = useDndContext();
  const rows = droppableContainers.toArray().map((c) => ({
    id: String(c.id),
    testid: c.node.current?.getAttribute('data-testid') ?? null,
    disabled: c.disabled,
  }));
  return <pre data-testid="probe">{JSON.stringify(rows)}</pre>;
}

function readProbe(): { id: string; testid: string | null; disabled: boolean }[] {
  return JSON.parse(screen.getByTestId('probe').textContent ?? '[]');
}

function Harness() {
  const [collapsed, setCollapsed] = React.useState(false);
  return (
    <DndContext>
      {collapsed ? <PoolCollapsedStrip count={0} onExpand={() => setCollapsed(false)} /> : null}
      <div className={collapsed ? 'hidden' : undefined}>
        <PoolGroupedByWeekday
          patients={[]}
          renderCard={() => null}
          onCollapse={() => setCollapsed(true)}
          dropDisabled={collapsed}
        />
      </div>
      <DroppableProbe />
    </DndContext>
  );
}

describe('PoolCollapsedStrip — 畳んだ保留プールへの drop', () => {
  it('開いているときは中の drop 領域が POOL_DROPPABLE_ID', () => {
    render(<Harness />);
    const pool = readProbe().find((r) => r.id === POOL_DROPPABLE_ID);
    expect(pool).toEqual({
      id: POOL_DROPPABLE_ID,
      testid: 'pool-grouped-by-weekday',
      disabled: false,
    });
  });

  it('畳むと細い帯が POOL_DROPPABLE_ID になり、中の drop 領域は別の id で無効', () => {
    render(<Harness />);
    fireEvent.click(screen.getByTestId('pool-collapse-button'));

    const rows = readProbe();
    const pool = rows.filter((r) => r.id === POOL_DROPPABLE_ID);
    expect(pool).toEqual([{ id: POOL_DROPPABLE_ID, testid: 'pool-expand-strip', disabled: false }]);
    const inner = rows.find((r) => r.testid === 'pool-grouped-by-weekday');
    expect(inner?.id).not.toBe(POOL_DROPPABLE_ID);
    expect(inner?.disabled).toBe(true);
  });

  it('もう一度開くと中の drop 領域が POOL_DROPPABLE_ID に戻る', () => {
    render(<Harness />);
    fireEvent.click(screen.getByTestId('pool-collapse-button'));
    fireEvent.click(screen.getByTestId('pool-expand-strip'));

    const pool = readProbe().filter((r) => r.id === POOL_DROPPABLE_ID);
    expect(pool).toEqual([
      { id: POOL_DROPPABLE_ID, testid: 'pool-grouped-by-weekday', disabled: false },
    ]);
  });

  it('編集できないときは帯も drop を受けない', () => {
    render(
      <DndContext>
        <PoolCollapsedStrip count={3} onExpand={() => {}} disabled />
        <DroppableProbe />
      </DndContext>,
    );
    expect(readProbe()).toEqual([
      { id: POOL_DROPPABLE_ID, testid: 'pool-expand-strip', disabled: true },
    ]);
    expect(screen.getByTestId('pool-expand-strip-count')).toHaveTextContent('3');
  });
});
