import { WebChannelBridge } from 'vsview-shared';

import type { JsToPyCommandMap, PyToJsCommandMap } from '../types';

export class BridgeService extends WebChannelBridge<PyToJsCommandMap, JsToPyCommandMap> {}
