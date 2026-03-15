import { MongoClient } from "mongodb";
import logger from "../utils/logger.js";

const clients = new Map();

const MongoWrapper = {
  async createClient(name, uri) {
    try {
      const client = new MongoClient(uri);
      await client.connect();
      clients.set(name, client);
      logger.success(`MongoDB connected: ${name}`);
      return client;
    } catch (error) {
      logger.error(`MongoDB connection failed: ${name}`, error);
      throw error;
    }
  },
  getClient(name) {
    return clients.get(name);
  },
  closeClient(name) {
    const client = clients.get(name);
    if (client) {
      client.close();
      clients.delete(name);
    }
  },
};

export default MongoWrapper;
