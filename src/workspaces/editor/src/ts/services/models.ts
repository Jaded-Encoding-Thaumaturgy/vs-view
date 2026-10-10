import type * as monaco from "monaco-editor";
import * as monacoEditor from "monaco-editor";
import type * as vscode from "vscode";

import { isSameResource } from "../utils/uri";

/**
 * Finds an existing Monaco text model matching the given URI.
 */
export function findExistingModel(
  resource: monaco.Uri | vscode.Uri,
): monaco.editor.ITextModel | undefined {
  const monacoUri = resource as monaco.Uri;
  const direct = monacoEditor.editor.getModel(monacoUri);
  if (direct && !direct.isDisposed()) {
    return direct;
  }

  for (const m of monacoEditor.editor.getModels()) {
    if (m.isDisposed()) continue;
    if (isSameResource(m.uri, resource)) {
      return m;
    }
  }

  return undefined;
}
