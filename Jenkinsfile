// Project settings. Timing values are in seconds.
def hostDir = '/docker/d2n'
def app = [
    name: 'd2n',
    testContainer: 'test-env-d2n',
    mypyTargets: 'main.py src config',
    stopTimeoutSeconds: 10,
    healthTimeoutSeconds: 300,
    healthPollSeconds: 5,
    dockerRunArgs: [
        '--volume=/var/run/docker.sock:/var/run/docker.sock:ro',
        "--volume=${hostDir}/config:/app/config",
        "--volume=${hostDir}/logs:/app/logs",
        "--volume=${hostDir}/data:/app/data",
        '--env=DOCKER_API_URL=unix:///var/run/docker.sock',
        '--env=NOTION_API_KEY',
        '--env=LOG_LEVEL=INFO',
    ],
    credentialIds: [NOTION_API_KEY: 'D2N_NOTION_API_KEY'],
]

// Shared pipeline
pipeline {
    agent {
        label 'jenkins_agent'
    }

    tools {
        dockerTool 'docker_default'
    }

    options {
        timestamps()
        disableConcurrentBuilds()
    }

    environment {
        WEBHOOK_URL = credentials('DISCORD_METATRON')
        PROJECT_NAME = "${app.name}"
        PROJECT_STATUS = 'stable'
        IMAGE_TAG = "${PROJECT_STATUS}-${BUILD_NUMBER}"
        TZ = 'Asia/Seoul'
        D2N_DATABASE = 'Jenkins'
        NETWORK = 'net_outbound'

        TEST_CONTAINER = "${app.testContainer}"
        TEST_IMAGE = 'python:3.14-trixie'
        TEST_REQUIREMENTS = 'requirements-dev.txt'
        MYPY_TARGETS = "${app.mypyTargets}"

        DOCKER_STOP_TIMEOUT = "${app.stopTimeoutSeconds}"
        HEALTH_POLL_SECONDS = "${app.healthPollSeconds}"
    }

    stages {
        stage('Test') {
            steps {
                sh '''
                    docker rm -f "$TEST_CONTAINER" || true
                    docker create --name "$TEST_CONTAINER" --network "$NETWORK" \\
                        --env TEST_REQUIREMENTS --env MYPY_TARGETS \\
                        -w /workspace "$TEST_IMAGE" /bin/sh -ec '
                            python -m venv .venv
                            . .venv/bin/activate
                            pip install --no-cache-dir -r "$TEST_REQUIREMENTS"
                            pytest
                            set -f
                            # MYPY_TARGETS contains space-separated paths.
                            # shellcheck disable=SC2086
                            mypy $MYPY_TARGETS
                        '
                    docker cp . "$TEST_CONTAINER:/workspace/"
                    docker start -a "$TEST_CONTAINER"
                    test_container_exit_code=$(docker inspect --format='{{.State.ExitCode}}' "$TEST_CONTAINER")
                    test "$test_container_exit_code" -eq 0
                '''
            }
            post {
                always {
                    sh 'docker rm -f "$TEST_CONTAINER" || true'
                }
            }
        }

        stage('Docker build') {
            steps {
                sh '''
                    DOCKER_BUILDKIT=0 docker build --network "$NETWORK" -t "$PROJECT_NAME:$IMAGE_TAG" .
                    docker tag "$PROJECT_NAME:$IMAGE_TAG" "$PROJECT_NAME:latest"
                '''
            }
        }

        stage('Deploy') {
            steps {
                script {
                    def bindings = app.credentialIds.collect { variable, credentialId ->
                        string(credentialsId: credentialId, variable: variable)
                    }
                    withCredentials(bindings) {
                        withEnv(["APP_DOCKER_ARGS=${app.dockerRunArgs.join('\n')}"]) {
                            env.DEPLOYED_CONTAINER_ID = sh(script: '''
                                # Preserve spaces and pass each configured option as one argument.
                                set --
                                while IFS= read -r option; do
                                    if [ -n "$option" ]; then
                                        set -- "$@" "$option"
                                    fi
                                done <<EOF
$APP_DOCKER_ARGS
EOF
                                if docker container inspect "$PROJECT_NAME" >/dev/null 2>&1; then
                                    docker stop --timeout "$DOCKER_STOP_TIMEOUT" "$PROJECT_NAME" >&2
                                    docker rm "$PROJECT_NAME" >&2
                                fi

                                DOCKER_BUILD_TAG="$IMAGE_TAG" docker run -d \\
                                    --name "$PROJECT_NAME" \\
                                    --env TZ \\
                                    --label "d2n.enabled=true" \\
                                    --label "d2n.database=$D2N_DATABASE" \\
                                    --label "com.centurylinklabs.watchtower.enable=false" \\
                                    --restart unless-stopped \\
                                    --stop-timeout "$DOCKER_STOP_TIMEOUT" \\
                                    --network "$NETWORK" \\
                                    "$@" \\
                                    "$PROJECT_NAME:latest"
                            ''', returnStdout: true).trim()
                        }
                    }
                }
            }
        }

        stage('Verify new docker container') {
            steps {
                script {
                    if (!env.DEPLOYED_CONTAINER_ID) {
                        error('Docker run did not return a container ID.')
                    }
                    try {
                        timeout(time: app.healthTimeoutSeconds, unit: 'SECONDS') {
                            sh '''
                                while :; do
                                    if ! state=$(docker inspect --format='{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}} {{.RestartCount}}' "$DEPLOYED_CONTAINER_ID"); then
                                        echo "Docker inspect failed for $DEPLOYED_CONTAINER_ID." >&2
                                        exit 1
                                    fi
                                    case "$state" in
                                        'running healthy 0')
                                            echo "Container $DEPLOYED_CONTAINER_ID is healthy."
                                            exit 0
                                            ;;
                                        'running starting 0') sleep "$HEALTH_POLL_SECONDS" ;;
                                        *)
                                            echo "Container $DEPLOYED_CONTAINER_ID state: $state" >&2
                                            exit 1
                                            ;;
                                    esac
                                done
                            '''
                        }
                    } catch (org.jenkinsci.plugins.workflow.steps.FlowInterruptedException interruption) {
                        if (interruption.getMessage() == 'Timeout has been exceeded') {
                            error("Container did not become healthy within ${app.healthTimeoutSeconds} seconds.")
                        }
                        throw interruption
                    }
                }
            }
        }

        stage('Cleanup dangling images') {
            steps {
                sh 'docker image prune -f || true'
            }
        }
    }

    post {
        success {
            discordSend description: 'Build Success',
                        footer: 'Application Deployed Successfully',
                        link: env.BUILD_URL,
                        result: currentBuild.currentResult,
                        title: "${env.JOB_NAME} #${BUILD_NUMBER}",
                        webhookURL: env.WEBHOOK_URL
        }
        failure {
            discordSend description: 'Build Fail',
                        footer: 'Application Deployed Failed',
                        link: env.BUILD_URL,
                        result: currentBuild.currentResult,
                        title: "${env.JOB_NAME} #${BUILD_NUMBER}",
                        webhookURL: env.WEBHOOK_URL
        }
    }
}
