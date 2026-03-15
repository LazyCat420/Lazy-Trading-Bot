import crypto from "crypto";
import MinioWrapper from "../wrappers/MinioWrapper.js";
import logger from "../utils/logger.js";

/**
 * MIME type → file extension map for common file types.
 */
const MIME_TO_EXT = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/gif": "gif",
  "image/webp": "webp",
  "image/svg+xml": "svg",
  "audio/wav": "wav",
  "audio/mpeg": "mp3",
  "audio/mp3": "mp3",
  "audio/webm": "webm",
  "audio/ogg": "ogg",
  "video/mp4": "mp4",
  "video/webm": "webm",
  "video/ogg": "ogg",
  "video/quicktime": "mov",
  "application/pdf": "pdf",
  "text/plain": "txt",
  "application/json": "json",
};

/**
 * FileService — abstracts file storage with MinIO primary / MongoDB inline fallback.
 *
 * When MinIO is available, files are uploaded to the bucket and a lightweight
 * reference string `minio://files/<uuid>.<ext>` is returned.
 *
 * When MinIO is unavailable, the original base64 data URL is returned unchanged,
 * so it continues to be stored inline in MongoDB.
 */
const FileService = {
  /**
   * Whether external (MinIO) storage is active.
   */
  isExternalStorage() {
    return MinioWrapper.isAvailable();
  },

  /**
   * Upload a file from a base64 data URL.
   * @param {string} dataUrl - e.g. "data:image/png;base64,iVBOR..."
   * @param {"uploads"|"generations"} [category="uploads"] - folder to store in
   * @param {string} [project] - project name for path organization
   * @param {string} [username] - username for path organization
   * @returns {Promise<{ ref: string, size: number, contentType: string }>}
   *   ref is either `minio://...` or the original dataUrl.
   */
  async uploadFile(
    dataUrl,
    category = "uploads",
    project = null,
    username = null,
  ) {
    // If MinIO is not available, return the data URL as-is (MongoDB inline)
    if (!MinioWrapper.isAvailable()) {
      const size = Math.round((dataUrl.length * 3) / 4); // rough base64 → bytes
      return { ref: dataUrl, size, contentType: "application/octet-stream" };
    }

    // Parse the data URL
    const match = dataUrl.match(/^data:([^;]+);base64,(.+)$/);
    if (!match) {
      // Not a data URL — return as-is (could be a plain URL or already a ref)
      return { ref: dataUrl, size: 0, contentType: "application/octet-stream" };
    }

    const contentType = match[1];
    const base64Data = match[2];
    const buffer = Buffer.from(base64Data, "base64");
    const ext = MIME_TO_EXT[contentType] || "bin";

    // Build path: projects/{project}/{username}/{category}/{uuid}.{ext}
    // Falls back to flat {category}/{uuid}.{ext} when project/username not provided
    let key;
    if (project && username) {
      key = `projects/${project}/${username}/${category}/${crypto.randomUUID()}.${ext}`;
    } else {
      key = `${category}/${crypto.randomUUID()}.${ext}`;
    }

    await MinioWrapper.upload(key, buffer, contentType);
    logger.info(
      `FileService: uploaded ${key} (${buffer.length} bytes, ${contentType})`,
    );

    return {
      ref: `minio://${key}`,
      size: buffer.length,
      contentType,
    };
  },

  /**
   * Get a file stream from a MinIO reference.
   * @param {string} key - The object key (without the "minio://" prefix)
   * @returns {Promise<{ stream: import('stream').Readable, contentType: string } | null>}
   */
  async getFile(key) {
    if (!MinioWrapper.isAvailable()) return null;

    try {
      const stat = await MinioWrapper.stat(key);
      const stream = await MinioWrapper.get(key);
      return {
        stream,
        contentType:
          stat.metaData?.["content-type"] || "application/octet-stream",
      };
    } catch (error) {
      logger.error(`FileService: failed to get ${key}: ${error.message}`);
      return null;
    }
  },

  /**
   * Check if a string is a MinIO reference.
   * @param {string} ref
   * @returns {boolean}
   */
  isMinioRef(ref) {
    return typeof ref === "string" && ref.startsWith("minio://");
  },

  /**
   * Extract the object key from a MinIO reference.
   * @param {string} ref - e.g. "minio://files/abc-123.png"
   * @returns {string} - e.g. "files/abc-123.png"
   */
  extractKey(ref) {
    return ref.replace("minio://", "");
  },
};

export default FileService;
