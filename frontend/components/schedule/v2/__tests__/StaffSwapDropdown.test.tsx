/**
 * StaffSwapDropdown テスト (W15-FE D-3).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const mockMutateAsync = vi.fn();
vi.mock('@/lib/queries/courses', () => ({
  useUpdateCourse: () => ({ mutateAsync: mockMutateAsync, isPending: false }),
}));

vi.mock('@/components/ui/popover', () => ({
  Popover: ({ children, open }: { children: React.ReactNode; open?: boolean }) => (
    <div data-popover-open={open ? 'true' : 'false'}>{children}</div>
  ),
  PopoverTrigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  PopoverContent: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="popover-content">{children}</div>
  ),
}));

vi.mock('sonner', () => ({
  toast: { warning: vi.fn(), success: vi.fn(), error: vi.fn() },
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: null, status: 'unauthenticated' }),
}));

vi.mock('lucide-react', () => ({
  ChevronDown: () => <span data-testid="chevron-down" />,
  User: () => <span data-testid="user-icon" />,
}));

vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

import { StaffSwapDropdown } from '../StaffSwapDropdown';
import type { StaffRead } from '@/lib/schemas/staff';

const sampleStaff: StaffRead[] = [
  {
    id: 'staff-1',
    name: '山本',
    code: 'YAM',
    status: 'active',
    role: 'staff',
    is_trainee: false,
  } as unknown as StaffRead,
  {
    id: 'staff-2',
    name: '中島',
    code: 'NAK',
    status: 'active',
    role: 'staff',
    is_trainee: false,
  } as unknown as StaffRead,
];

describe('StaffSwapDropdown', () => {
  beforeEach(() => {
    mockMutateAsync.mockReset();
    mockMutateAsync.mockResolvedValue({ id: 'course-1', assigned_staff_id: 'staff-2' });
  });

  it('現在のスタッフ code を trigger に表示する', () => {
    render(
      <StaffSwapDropdown
        courseId="course-1"
        currentStaffId="staff-1"
        staffList={sampleStaff}
        canEdit={true}
      />,
    );
    expect(screen.getByLabelText(/担当スタッフ/)).toHaveTextContent('YAM');
  });

  it('staff_id 未割当時は "未割当" 表示', () => {
    render(
      <StaffSwapDropdown
        courseId="course-1"
        currentStaffId={null}
        staffList={sampleStaff}
        canEdit={true}
      />,
    );
    expect(screen.getByLabelText(/担当スタッフ/)).toHaveTextContent('未割当');
  });

  it('canEdit=false で trigger ボタンが disabled', () => {
    render(
      <StaffSwapDropdown
        courseId="course-1"
        currentStaffId="staff-1"
        staffList={sampleStaff}
        canEdit={false}
      />,
    );
    const btn = screen.getByLabelText(/担当スタッフ/);
    expect(btn).toBeDisabled();
  });

  it('スタッフ選択で useUpdateCourse の mutateAsync が呼ばれる', async () => {
    render(
      <StaffSwapDropdown
        courseId="course-1"
        currentStaffId="staff-1"
        staffList={sampleStaff}
        canEdit={true}
      />,
    );
    // popover 内のスタッフボタン (中島) をクリック
    const nakajimaBtn = screen.getAllByRole('button').find((b) => b.textContent?.includes('NAK'));
    expect(nakajimaBtn).toBeDefined();
    fireEvent.click(nakajimaBtn!);
    await waitFor(() => {
      expect(mockMutateAsync).toHaveBeenCalledOnce();
    });
    expect(mockMutateAsync.mock.calls[0][0]).toEqual({
      id: 'course-1',
      patch: { assigned_staff_id: 'staff-2' },
    });
  });

  it('courseId が null のときは mutate を呼ばず警告のみ', async () => {
    render(
      <StaffSwapDropdown
        courseId={null}
        currentStaffId={null}
        staffList={sampleStaff}
        canEdit={true}
      />,
    );
    const nakajimaBtn = screen.getAllByRole('button').find((b) => b.textContent?.includes('NAK'));
    fireEvent.click(nakajimaBtn!);
    await new Promise((r) => setTimeout(r, 0));
    expect(mockMutateAsync).not.toHaveBeenCalled();
  });
});
