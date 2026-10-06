'use strict';

const MAX_DEPTH = 64;
const assertDepth = depth => {
  if (depth > MAX_DEPTH) {
    throw new SyntaxError(`Brace pattern nesting exceeds maximum depth (${MAX_DEPTH})`);
  }
};

module.exports = { MAX_DEPTH, assertDepth };
