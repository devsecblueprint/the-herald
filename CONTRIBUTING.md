# Contributing to Discord Bot

Thank you for considering contributing to the **Discord Bot** project! Contributions are what make the open-source community a fantastic place to learn, inspire, and create. We welcome contributions of all kinds, from reporting issues to suggesting features or contributing code.

## Table of Contents

1. [Getting Started](#getting-started)
2. [Prerequisites](#prerequisites)
3. [Setting Up Your Environment](#setting-up-your-environment)
4. [Contributing Code](#contributing-code)
5. [Submitting a Pull Request](#submitting-a-pull-request)
6. [Code of Conduct](#code-of-conduct)

## Getting Started

1. Fork the repository and clone it locally:

   ```bash
   git clone https://github.com/The-DevSec-Blueprint/discord-bot.git
   cd discord-bot
   ```

2. Create a new branch for your changes:

   ```bash
   git checkout -b feature/my-feature
   ```

## Prerequisites

Ensure you have the following installed on your local machine:

- **Docker**
- **Python 3.12**
- **Terraform CLI**

You may also need:

- A text editor or IDE (e.g., VSCode, PyCharm)
- Linter tools: **Black** and **Pylint**
- Linter for Terraform files: Use `terraform fmt` and `terraform validate`

## Setting Up Your Environment

1. **Set up a Python virtual environment:**

   ```bash
   python3.12 -m venv venv
   source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
   pip install -r requirements.txt
   ```

2. **Install and configure Docker.**
   Ensure Docker is running on your system.

3. **Set up Terraform Cloud:**

   - Update the organization name in the Terraform configurations (`terraform.tfvars`) to match your local or test environment.
   - Initialize and validate Terraform:

     ```bash
     terraform init
     terraform validate
     ```

4. **Lint and format Python code:**

   - Use **Black** for formatting:

     ```bash
     black .
     ```

   - Use **Pylint** for linting:

     ```bash
     pylint path/to/your/files
     ```

5. **Lint Terraform files:**

   - Format Terraform files:

     ```bash
     terraform fmt
     ```

   - Validate Terraform configuration:

     ```bash
     terraform validate
     ```

## Contributing Code

1. **Write clean and tested code.**
   Follow the project structure and adhere to Python best practices.

2. **Run linters:**

   - Format Python code using **Black**.
   - Lint Python code using **Pylint** and resolve any issues.
   - Format Terraform Code

3. **Test your changes:**
   Make sure they work locally and be ready to provide proof.

4. **Document your changes:**
   Update README or related documentation if necessary.

## Submitting a Pull Request

1. Push your changes to your fork:

   ```bash
   git push origin feature/my-feature
   ```

2. Open a pull request on the main repository, providing:

   - A clear title and description of your changes.
   - References to any relevant issues.

3. Be responsive to feedback and update your pull request as necessary.

## Code of Conduct

This project adheres to a [Code of Conduct](./CODE_OF_CONDUCT.md). By participating, you agree to uphold this code and contribute to a positive and inclusive environment.

Thank you for contributing to **Discord Bot**! 🚀
