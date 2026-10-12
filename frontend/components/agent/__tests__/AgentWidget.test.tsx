/**
 * AgentWidget の出し分け: 管理者 + 試験の許可 + 機能の旗の 3 つが揃った時だけ描く。
 */
import * as React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('next-auth/react', () => ({ useSession: vi.fn() }));
vi.mock('../useAgentFlag', () => ({ useAgentFlag: vi.fn() }));

import { useSession } from 'next-auth/react';

import { AgentWidget } from '../AgentWidget';
import { createMockRelay } from '../mockRelay';
import { useAgentFlag } from '../useAgentFlag';

function setUser(role: string, id = 'u-1') {
  (useSession as Mock).mockReturnValue({
    data: { user: { id, role, email: `${id}@example.com` }, accessToken: 't' },
    status: 'authenticated',
  });
}

describe('AgentWidget gate', () => {
  beforeEach(() => {
    vi.stubEnv('NEXT_PUBLIC_AGENT_PILOT_USERS', 'u-1');
    (useAgentFlag as Mock).mockReturnValue(true);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.clearAllMocks();
  });

  it('旗が OFF なら何も描かない', () => {
    setUser('admin');
    (useAgentFlag as Mock).mockReturnValue(false);
    const { container } = render(<AgentWidget relay={createMockRelay({ speed: 0 })} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('一般職員(staff)には何も描かない', () => {
    setUser('staff');
    const { container } = render(<AgentWidget relay={createMockRelay({ speed: 0 })} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('許可リストに無い管理者には何も描かない', () => {
    setUser('admin', 'u-9');
    const { container } = render(<AgentWidget relay={createMockRelay({ speed: 0 })} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('許可リストが空なら誰にも出さない', () => {
    vi.stubEnv('NEXT_PUBLIC_AGENT_PILOT_USERS', '');
    setUser('admin');
    const { container } = render(<AgentWidget relay={createMockRelay({ speed: 0 })} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('管理者 + 許可 + 旗 ON ならたたんだ丸が出る', () => {
    setUser('admin');
    render(<AgentWidget relay={createMockRelay({ speed: 0 })} />);
    expect(screen.getByRole('button', { name: 'らく助を開く' })).toBeInTheDocument();
  });
});
