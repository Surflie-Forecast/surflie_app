# GitHub Secrets Setup Guide

This document explains how to configure GitHub Secrets for the Copernicus Marine Service credentials used in the automated forecast pipeline.

## Why Use GitHub Secrets?

GitHub Secrets provide a secure way to store sensitive information like API credentials. They are:
- **Encrypted** at rest and in transit
- **Never exposed** in logs or output
- **Only accessible** during workflow execution
- **Safer** than hardcoding credentials in your code

## Setup Instructions

### 1. Navigate to Repository Settings

1. Go to your GitHub repository: `https://github.com/pcarbomestre/surflie`
2. Click on **Settings** (top navigation bar)
3. In the left sidebar, expand **Secrets and variables**
4. Click on **Actions**

### 2. Add Copernicus Username

1. Click **New repository secret** (green button)
2. Set the following:
   - **Name**: `COPERNICUS_USERNAME`
   - **Secret**: `pcarbomestre`
3. Click **Add secret**

### 3. Add Copernicus Password

1. Click **New repository secret** again
2. Set the following:
   - **Name**: `COPERNICUS_PASSWORD`
   - **Secret**: `3zR#j#WYwAYYQK`
3. Click **Add secret**

### 4. Verify Configuration

Your secrets page should now show:
- ✓ `COPERNICUS_USERNAME`
- ✓ `COPERNICUS_PASSWORD`

## Testing the Workflow

1. Go to the **Actions** tab in your repository
2. Select the "Update Surf Forecast Data" workflow
3. Click **Run workflow** → **Run workflow**
4. Wait for the workflow to complete
5. Check the logs to verify successful authentication

## Local Development

For local development, you can set environment variables in your shell:

```bash
export COPERNICUS_USERNAME="pcarbomestre"
export COPERNICUS_PASSWORD="3zR#j#WYwAYYQK"
```

Or create a `.env` file (make sure it's in `.gitignore`):

```bash
COPERNICUS_USERNAME=pcarbomestre
COPERNICUS_PASSWORD=3zR#j#WYwAYYQK
```

## Security Notes

- Never commit credentials to your repository
- Secrets are encrypted and cannot be viewed after creation
- Only repository administrators can manage secrets
- Secrets are automatically redacted from workflow logs
