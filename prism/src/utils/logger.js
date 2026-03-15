const COLORS = {
  reset: "\x1b[0m",
  red: "\x1b[31m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  blue: "\x1b[34m",
  magenta: "\x1b[35m",
  cyan: "\x1b[36m",
  gray: "\x1b[90m",
};

function timestamp() {
  return new Date().toISOString();
}

const logger = {
  info(message, ...args) {
    console.log(
      `${COLORS.gray}[${timestamp()}]${COLORS.reset} ${COLORS.blue}ℹ${COLORS.reset} ${message}`,
      ...args,
    );
  },
  success(message, ...args) {
    console.log(
      `${COLORS.gray}[${timestamp()}]${COLORS.reset} ${COLORS.green}✓${COLORS.reset} ${message}`,
      ...args,
    );
  },
  warn(message, ...args) {
    console.warn(
      `${COLORS.gray}[${timestamp()}]${COLORS.reset} ${COLORS.yellow}⚠${COLORS.reset} ${message}`,
      ...args,
    );
  },
  error(message, ...args) {
    console.error(
      `${COLORS.gray}[${timestamp()}]${COLORS.reset} ${COLORS.red}✗${COLORS.reset} ${message}`,
      ...args,
    );
  },
  provider(provider, action, ...args) {
    console.log(
      `${COLORS.gray}[${timestamp()}]${COLORS.reset} ${COLORS.magenta}◆${COLORS.reset} ${COLORS.cyan}[${provider}]${COLORS.reset} ${action}`,
      ...args,
    );
  },
};

export default logger;
