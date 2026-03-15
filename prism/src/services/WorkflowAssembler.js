import { getModelByName } from "../config.js";

// ============================================================
// LAYOUT CONSTANTS
// ============================================================

const STEP_WIDTH = 1250;
const INPUT_X_OFFSET = 0;
const CONV_X_OFFSET = 350;
const MODEL_X_OFFSET = 650;
const VIEWER_X_OFFSET = MODEL_X_OFFSET + 350;

// ============================================================
// HELPERS
// ============================================================

/**
 * Build compound port IDs for a conversation input node.
 * Format: "{messageIndex}.{modality}" e.g. "0.text", "1.text", "1.image"
 */
function buildConversationPorts(messages, supportedModalities = ["text"]) {
  const ports = [];
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    ports.push(`${i}.text`);
    if (msg.role === "user" || msg.role === "assistant") {
      for (const mod of supportedModalities) {
        if (mod !== "text") {
          ports.push(`${i}.${mod}`);
        }
      }
    }
  }
  return ports;
}

/**
 * Resolve a model's input/output types from the Prism config.
 * Falls back to step-derived values if the model isn't found in config.
 */
function resolveModelModalities(step) {
  const configModel = getModelByName(step.model);
  const isImageGen = step.outputType === "image";

  if (configModel) {
    return {
      inputTypes: configModel.inputTypes || ["text"],
      outputTypes: configModel.outputTypes || ["text"],
      rawInputTypes: configModel.inputTypes || ["text"],
      modelType: configModel.modelType || (isImageGen ? "image" : "conversation"),
      supportsSystemPrompt: configModel.supportsSystemPrompt !== undefined
        ? configModel.supportsSystemPrompt
        : configModel.outputTypes?.includes("text") ?? true,
    };
  }

  // Fallback: derive from step data
  return {
    inputTypes: ["text"],
    outputTypes: isImageGen ? ["text", "image"] : ["text"],
    rawInputTypes: ["text"],
    modelType: isImageGen ? "image" : "conversation",
    supportsSystemPrompt: true,
  };
}

// ============================================================
// MAIN ASSEMBLER
// ============================================================

/**
 * Assemble a visual workflow graph from raw step data.
 *
 * Each step produces:
 *   1. Text Input nodes (system prompt + user message)
 *   2. Conversation Input node (groups messages with compound ports)
 *   3. Model node (AI model with config-derived modality ports)
 *   4. Output Viewer node (displays the model's text/image output)
 *   5. Chain connections (previous model → this model for multi-step pipelines)
 *
 * @param {Array} steps - Raw step data from the client
 * @returns {{ nodes, connections, nodeResults, nodeStatuses }}
 */
function assembleGraph(steps) {
  if (!Array.isArray(steps) || steps.length === 0) {
    return { nodes: [], connections: [], nodeResults: {}, nodeStatuses: {} };
  }

  const allNodes = [];
  const allConnections = [];
  const nodeResults = {};
  const nodeStatuses = {};

  steps.forEach((step, i) => {
    const baseX = 80 + i * STEP_WIDTH;
    const baseY = 80;
    const stepPrefix = `s${i}`;
    let inputY = baseY;

    const modalities = resolveModelModalities(step);

    // ── 1. Text Input: System Prompt ──
    const sysId = `${stepPrefix}_sys`;
    if (step.systemPrompt) {
      allNodes.push({
        id: sysId,
        nodeType: "input",
        modality: "text",
        content: step.systemPrompt,
        inputTypes: [],
        outputTypes: ["text"],
        position: { x: baseX + INPUT_X_OFFSET, y: inputY },
      });
      inputY += 200;
    }

    // ── 2. Text Input: User Message ──
    const userMsgId = `${stepPrefix}_user`;
    if (step.input) {
      allNodes.push({
        id: userMsgId,
        nodeType: "input",
        modality: "text",
        content: step.input,
        inputTypes: [],
        outputTypes: ["text"],
        position: { x: baseX + INPUT_X_OFFSET, y: inputY },
      });
      inputY += 200;
    }

    // ── 3. Conversation Node ──
    const convId = `${stepPrefix}_conv`;
    const messages = [];
    if (step.systemPrompt) messages.push({ role: "system", content: step.systemPrompt });
    const userMsg = { role: "user", content: step.input || "" };
    messages.push(userMsg);
    if (step.output) {
      const assistantMsg = { role: "assistant", content: step.output };
      if (step.outputImageRef) assistantMsg.images = [step.outputImageRef];
      messages.push(assistantMsg);
    }

    // Derive conversation supported modalities from the model's raw input types
    const supportedModalities = (modalities.rawInputTypes || ["text"])
      .filter((t) => t !== "conversation");
    const convInputTypes = buildConversationPorts(messages, supportedModalities);

    allNodes.push({
      id: convId,
      nodeType: "input",
      modality: "conversation",
      messages,
      supportedModalities,
      inputTypes: convInputTypes,
      outputTypes: ["conversation"],
      position: { x: baseX + CONV_X_OFFSET, y: baseY + 100 },
    });

    // Wire inputs → conversation node
    const sysIdx = 0;
    const userIdx = step.systemPrompt ? 1 : 0;

    if (step.systemPrompt) {
      allConnections.push({
        id: `${stepPrefix}_sys_to_conv`,
        sourceNodeId: sysId,
        targetNodeId: convId,
        sourceModality: "text",
        targetModality: `${sysIdx}.text`,
      });
    }
    if (step.input) {
      allConnections.push({
        id: `${stepPrefix}_user_to_conv`,
        sourceNodeId: userMsgId,
        targetNodeId: convId,
        sourceModality: "text",
        targetModality: `${userIdx}.text`,
      });
    }

    // ── 4. Model Node ──
    const modelId = `${stepPrefix}_model`;
    allNodes.push({
      id: modelId,
      modelName: step.model || "unknown",
      provider: step.type?.toLowerCase() || "unknown",
      displayName: step.label || step.model || "Step",
      modelType: modalities.modelType,
      inputTypes: ["conversation"],
      rawInputTypes: modalities.rawInputTypes,
      outputTypes: modalities.outputTypes,
      supportsSystemPrompt: modalities.supportsSystemPrompt,
      position: { x: baseX + MODEL_X_OFFSET, y: baseY + 100 },
      stepMeta: {
        duration: step.duration,
        timestamp: step.timestamp,
        index: step.index,
      },
    });

    // Wire conversation → model
    allConnections.push({
      id: `${stepPrefix}_conv_to_model`,
      sourceNodeId: convId,
      targetNodeId: modelId,
      sourceModality: "conversation",
      targetModality: "conversation",
    });

    // Mark model node as done with results
    nodeStatuses[modelId] = "done";
    const result = {};
    if (step.output) result.text = step.output;
    if (step.outputImageRef) result.image = step.outputImageRef;
    nodeResults[modelId] = result;

    // ── 5. Output Viewer ──
    const viewerId = `${stepPrefix}_viewer`;
    const viewerResult = {};
    if (step.output) viewerResult.text = step.output;
    if (step.outputImageRef) viewerResult.image = step.outputImageRef;

    allNodes.push({
      id: viewerId,
      nodeType: "viewer",
      modality: null,
      content: viewerResult.text || viewerResult.image || null,
      contentType: viewerResult.image ? "image" : viewerResult.text ? "text" : null,
      receivedOutputs: viewerResult,
      inputTypes: ["text", "image", "audio"],
      outputTypes: ["text", "image", "audio"],
      position: {
        x: baseX + VIEWER_X_OFFSET,
        y: baseY + 100,
      },
    });

    // Connect model outputs to viewer
    if (step.output) {
      allConnections.push({
        id: `${stepPrefix}_model_to_viewer_text`,
        sourceNodeId: modelId,
        targetNodeId: viewerId,
        sourceModality: "text",
        targetModality: "text",
      });
    }
    if (step.outputImageRef) {
      allConnections.push({
        id: `${stepPrefix}_model_to_viewer_image`,
        sourceNodeId: modelId,
        targetNodeId: viewerId,
        sourceModality: "image",
        targetModality: "image",
      });
    }

    nodeStatuses[viewerId] = "done";
    nodeResults[viewerId] = viewerResult;

    // ── 6. Chain from previous model → this model ──
    if (i > 0) {
      const prevModelId = `s${i - 1}_model`;
      allConnections.push({
        id: `chain_${i - 1}_to_${i}`,
        sourceNodeId: prevModelId,
        targetNodeId: modelId,
        sourceModality: "text",
        targetModality: "text",
      });
      // Add text input port on the model for the chain connection
      const modelNode = allNodes.find((n) => n.id === modelId);
      if (modelNode) modelNode.inputTypes.push("text");
    }
  });

  return { nodes: allNodes, connections: allConnections, nodeResults, nodeStatuses };
}

export { assembleGraph };
